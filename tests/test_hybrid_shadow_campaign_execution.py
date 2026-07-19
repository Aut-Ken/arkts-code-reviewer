from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import cast

import pytest

import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.code_analysis import (
    AnalysisResult,
    ChangeAtomInput,
    ChangedFileInput,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ContextPlanResult,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialUnavailableError,
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
    DeepSeekShadowHttpTransport,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    AITagShadowCampaignUnitArtifacts,
    build_ai_tag_shadow_campaign,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign_execution import (
    AITagShadowCampaignExecutionBundle,
    AITagShadowCampaignExecutionLimits,
    AITagShadowCampaignExecutionResult,
    AITagShadowCampaignLiveHarness,
    AITagShadowCampaignNonAttemptReceipt,
    AITagShadowCampaignRuntimeBinding,
    AITagShadowCampaignTrustedUpstream,
    AITagShadowCampaignUnitExecution,
    load_ai_tag_shadow_campaign_execution_result,
    load_ai_tag_shadow_campaign_non_attempt_receipt,
    load_ai_tag_shadow_campaign_unit_execution,
    seal_ai_tag_shadow_campaign_execution_result,
    seal_ai_tag_shadow_campaign_non_attempt_receipt,
    seal_ai_tag_shadow_campaign_unit_execution,
    verify_ai_tag_shadow_campaign_execution_result,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowTrustedPlanInputs,
)

_REPOSITORY = "campaign-execution-repo"
_BASE = "base-revision"
_HEAD = "head-revision"
_CREDENTIAL_SECRET = "CREDENTIAL_SECRET_MUST_NOT_PERSIST"
_MODEL_REASON_SECRET = "MODEL_REASON_SECRET_MUST_NOT_PERSIST"
_RAW_ERROR_SECRET = "RAW_ERROR_SECRET_MUST_NOT_PERSIST"


@dataclass(frozen=True)
class _Scenario:
    analysis: AnalysisResult
    context_plan: ContextPlanResult
    snapshots: dict[str, CodeSourceSnapshot]


@dataclass
class _Credential:
    marker: str
    configured: bool = True
    fail_on_get: bool = False
    secret: str = _CREDENTIAL_SECRET
    configured_checks: int = 0
    key_reads: int = 0

    @property
    def credential_scope_id(self) -> str:
        return _hash_id("deepseek-credential-scope", self.marker)

    def is_configured(self) -> bool:
        self.configured_checks += 1
        return self.configured

    def get_api_key(self) -> str:
        self.key_reads += 1
        if self.fail_on_get:
            raise DeepSeekCredentialUnavailableError("credential unavailable")
        return self.secret


@dataclass
class _EgressVerifier:
    expected_plan_id: str
    deny: bool = False
    calls: int = 0

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert approval_id.startswith("ai-egress-approval:sha256:")
        self.calls += 1
        if self.deny:
            raise AITagShadowAuthorizationError("egress_not_approved")


@dataclass
class _BudgetLedger:
    expected_plan_id: str
    deny: bool = False
    calls: int = 0

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert reservation_id.startswith("ai-budget-reservation:sha256:")
        self.calls += 1
        if self.deny:
            raise AITagShadowAuthorizationError("budget_not_reserved")


@dataclass
class _Transport:
    expected_plan_id: str
    outcome: DeepSeekHttpResponse | Exception
    calls: int = 0
    seen_api_keys: list[str] = field(default_factory=list)

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.plan_id == self.expected_plan_id
        assert plan.max_attempts == 1
        self.calls += 1
        self.seen_api_keys.append(api_key)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@dataclass(frozen=True)
class _CampaignSetup:
    scenario: _Scenario
    trusted_upstream: AITagShadowCampaignTrustedUpstream
    limits: AITagShadowCampaignExecutionLimits


@dataclass
class _RuntimeState:
    binding: AITagShadowCampaignRuntimeBinding
    credential: _Credential
    egress: _EgressVerifier
    budget: _BudgetLedger
    transport: _Transport | None
    raw_body: bytes | None


@dataclass(frozen=True)
class _MixedExecution:
    setup: _CampaignSetup
    states: dict[str, _RuntimeState]
    execution: AITagShadowCampaignExecutionBundle


def _hash_id(prefix: str, marker: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{marker}".encode()).hexdigest()
    return f"{prefix}:sha256:{digest}"


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository=_REPOSITORY,
            revision=revision,
            path=path,
            content_hash=f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
        ),
        content=content,
    )


def _scenario() -> _Scenario:
    path = "src/Page.ets"
    base = _snapshot(
        path,
        """@Entry
@Component
struct Page {
  first() {
    console.info("old first")
  }
  second() {
    console.info("old second")
  }
  third() {
    console.info("old third")
  }
  fourth() {
    console.info("old fourth")
  }
}
""",
        _BASE,
    )
    head = _snapshot(
        path,
        """@Entry
@Component
struct Page {
  first() {
    console.info("new first")
  }
  second() {
    console.info("new second")
  }
  third() {
    console.info("new third")
  }
  fourth() {
    console.info("new fourth")
  }
}
""",
        _HEAD,
    )
    atoms = tuple(
        ChangeAtomInput(
            kind="replacement",
            old_span=ReviewUnitSpan(line_number, line_number),
            new_span=ReviewUnitSpan(line_number, line_number),
            deleted_old_lines=(line_number,),
            added_new_lines=(line_number,),
        )
        for line_number in (5, 8, 11, 14)
    )
    change_set = normalize_change_set(
        repository=_REPOSITORY,
        base_revision=_BASE,
        head_revision=_HEAD,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=atoms,
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    context_plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    assert len(analysis.review_units) == 8
    return _Scenario(analysis, context_plan, snapshots)


def _campaign_setup() -> _CampaignSetup:
    scenario = _scenario()
    unit_ids = tuple(item.unit_id for item in scenario.analysis.review_units)
    campaign = build_ai_tag_shadow_campaign(
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=tuple(reversed(unit_ids)),
    )
    limits = AITagShadowCampaignExecutionLimits(
        max_units=len(campaign.units),
        max_total_wire_body_bytes=sum(
            len(item.plan.wire_body_json.encode()) for item in campaign.units
        ),
        max_total_output_tokens=sum(item.plan.wire_payload.max_tokens for item in campaign.units),
        max_total_response_bytes=sum(item.plan.max_response_bytes for item in campaign.units),
        campaign_wall_clock_cap_ms=sum(item.plan.wall_clock_timeout_ms for item in campaign.units),
    )
    trusted_upstream = AITagShadowCampaignTrustedUpstream(
        bundle=campaign,
        analysis_result=scenario.analysis,
        context_plan=scenario.context_plan,
        source_snapshots=scenario.snapshots,
        unit_ids=tuple(reversed(unit_ids)),
    )
    return _CampaignSetup(scenario, trusted_upstream, limits)


def _judgments(
    unit: AITagShadowCampaignUnitArtifacts,
    *,
    diagnostic_reason: str | None = None,
) -> tuple[dict[str, object], ...]:
    visible_line = unit.model_view.code.line_numbers[0]
    values: list[dict[str, object]] = []
    for contract in unit.request.tag_contract_views:
        if contract.tag_id == "has_logging":
            values.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "positive",
                    "evidence_lines": [visible_line],
                    "reason_code": "direct_unit_semantic_evidence",
                    "reason": diagnostic_reason or "The Unit writes a log message.",
                }
            )
        else:
            values.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "not_supported",
                    "evidence_lines": [],
                    "reason_code": "no_support_in_complete_view",
                    "reason": None,
                }
            )
    return tuple(values)


def _provider_body(
    unit: AITagShadowCampaignUnitArtifacts,
    *,
    response_marker: str,
    invalid_inner: bool = False,
    diagnostic_reason: str | None = None,
) -> bytes:
    judgments = list(_judgments(unit, diagnostic_reason=diagnostic_reason))
    if invalid_inner:
        judgments.pop()
    content = json.dumps(
        {"judgments": judgments},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return json.dumps(
        {
            "id": f"chatcmpl-campaign-execution-{response_marker}",
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {
                        "content": content,
                        "role": "assistant",
                        "reasoning_content": None,
                    },
                    "logprobs": None,
                }
            ],
            "created": 1_750_000_000,
            "model": "deepseek-v4-pro",
            "object": "chat.completion",
            "system_fingerprint": "fp-campaign-execution-test",
            "usage": {
                "completion_tokens": 50,
                "prompt_tokens": 100,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 25,
                "prompt_cache_miss_tokens": 75,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _claims(
    plan: AITagShadowDispatchPlan,
    *,
    credential_scope_id: str,
    marker: str,
) -> AITagShadowDispatchClaims:
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": plan.plan_id,
            "trust_domain_id": _hash_id("ai-shadow-trust-domain", marker),
            "egress_approval_id": _hash_id("ai-egress-approval", marker),
            "budget_reservation_id": _hash_id("ai-budget-reservation", marker),
            "credential_scope_id": credential_scope_id,
            "egress_scope": "exact_wire_body_sha256",
            "budget_scope": "one_attempt_worst_case_reserved",
            "qualification": "references_require_runtime_verification",
        }
    )


def _runtime_state(
    setup: _CampaignSetup,
    unit: AITagShadowCampaignUnitArtifacts,
    *,
    index: int,
    outcome: DeepSeekHttpResponse | Exception,
    deny_budget: bool = False,
    deny_egress: bool = False,
    include_transport: bool = True,
) -> _RuntimeState:
    marker = f"unit-{index}"
    credential = _Credential(marker)
    claims = _claims(
        unit.plan,
        credential_scope_id=credential.credential_scope_id,
        marker=marker,
    )
    egress = _EgressVerifier(unit.plan.plan_id, deny=deny_egress)
    budget = _BudgetLedger(unit.plan.plan_id, deny=deny_budget)
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=AITagShadowTrustedPlanInputs(
            envelope=unit.envelope,
            card=unit.card,
            context_policy=setup.trusted_upstream.context_policy,
            max_output_tokens=unit.plan.wire_payload.max_tokens,
            wall_clock_timeout_ms=unit.plan.wall_clock_timeout_ms,
            max_response_bytes=unit.plan.max_response_bytes,
        ),
        egress_verifier=egress,
        budget_ledger=budget,
    )
    transport = _Transport(unit.plan.plan_id, outcome) if include_transport else None
    binding = AITagShadowCampaignRuntimeBinding(
        claims=claims,
        gate=gate,
        transport=cast(DeepSeekShadowHttpTransport | None, transport),
    )
    return _RuntimeState(
        binding=binding,
        credential=credential,
        egress=egress,
        budget=budget,
        transport=transport,
        raw_body=outcome.body if isinstance(outcome, DeepSeekHttpResponse) else None,
    )


def _mixed_states(setup: _CampaignSetup) -> dict[str, _RuntimeState]:
    units = setup.trusted_upstream.bundle.units
    states: dict[str, _RuntimeState] = {}
    for index, unit in enumerate(units):
        if index == 0:
            outcome: DeepSeekHttpResponse | DeepSeekHttpTransportError = DeepSeekHttpResponse(
                200,
                _provider_body(unit, response_marker="valid-first"),
                None,
                10,
            )
        elif index == 1:
            outcome = DeepSeekHttpResponse(
                200,
                _provider_body(unit, response_marker="inner-invalid", invalid_inner=True),
                None,
                11,
            )
        elif index == 2:
            outcome = DeepSeekHttpResponse(
                200,
                json.dumps({"outer_error": _RAW_ERROR_SECRET}).encode(),
                None,
                12,
            )
        elif index == 3:
            outcome = DeepSeekHttpResponse(
                429,
                json.dumps({"provider_error": _RAW_ERROR_SECRET}).encode(),
                1_000,
                13,
            )
        elif index == 4:
            outcome = DeepSeekHttpTransportError("provider_timeout", latency_ms=14)
        else:
            outcome = DeepSeekHttpResponse(
                200,
                _provider_body(
                    unit,
                    response_marker=f"unused-or-final-{index}",
                    diagnostic_reason=(_MODEL_REASON_SECRET if index == len(units) - 1 else None),
                ),
                None,
                15 + index,
            )
        state = _runtime_state(
            setup,
            unit,
            index=index,
            outcome=outcome,
            deny_budget=index == 5,
            deny_egress=index == 6,
        )
        states[unit.plan.plan_id] = state
    return states


def _bindings(states: dict[str, _RuntimeState]) -> dict[str, AITagShadowCampaignRuntimeBinding]:
    return {plan_id: state.binding for plan_id, state in states.items()}


def _evidence(
    execution: AITagShadowCampaignExecutionBundle,
) -> dict[str, object]:
    return {item.plan_id: item for item in execution.unit_evidence}


def _raw_response_mapping(mixed: _MixedExecution) -> dict[str, bytes]:
    expected = {
        item.plan_id
        for item in mixed.execution.result.units
        if item.attempt_transport_status == "response_received"
    }
    return {plan_id: cast(bytes, mixed.states[plan_id].raw_body) for plan_id in expected}


@pytest.fixture(scope="module")
def campaign_setup() -> _CampaignSetup:
    return _campaign_setup()


@pytest.fixture(scope="module")
def mixed_execution(campaign_setup: _CampaignSetup) -> _MixedExecution:
    states = _mixed_states(campaign_setup)
    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=campaign_setup.limits,
        allow_injected_transport=True,
    )
    return _MixedExecution(campaign_setup, states, execution)


def test_mixed_campaign_continues_canonically_and_enforces_zero_attempt_matrix(
    mixed_execution: _MixedExecution,
) -> None:
    campaign = mixed_execution.setup.trusted_upstream.bundle
    result = mixed_execution.execution.result

    assert tuple(item.plan_id for item in result.units) == tuple(
        item.plan.plan_id for item in campaign.units
    )
    assert tuple(item.attempt_outcome for item in result.units) == (
        "valid_shape",
        "invalid_output_inner",
        "invalid_output_outer",
        "provider_rate_limited",
        "provider_timeout",
        None,
        None,
        "valid_shape",
    )
    assert tuple(item.dispatch_disposition for item in result.units) == (
        "attempted",
        "attempted",
        "attempted",
        "attempted",
        "attempted",
        "skipped_budget",
        "not_run",
        "attempted",
    )
    assert result.units[5].local_non_attempt_reason == "budget_not_reserved"
    assert result.units[6].local_non_attempt_reason == "egress_not_approved"

    for record in result.units[5:7]:
        assert record.attempt_count == 0
        assert record.non_attempt_receipt_id is not None
        assert record.attempt_receipt_id is None
        assert record.provider_response_receipt_id is None
        assert record.response_validation_id is None
        assert record.outer_diagnostic_id is None
        assert record.observation_id is None
        assert record.attempt_transport_status is None
        assert record.transport_evidence is None
        assert record.network_observation is None

    counts = result.counts
    assert counts.planned_unit_count == 8
    assert counts.attempted_unit_count == 6
    assert counts.skipped_budget_unit_count == 1
    assert counts.not_run_unit_count == 1
    assert counts.valid_shape_count == 2
    assert counts.inner_invalid_count == 1
    assert counts.outer_invalid_count == 1
    assert counts.provider_rate_limited_count == 1
    assert counts.provider_timeout_count == 1
    assert counts.injected_transport_attempt_count == 6
    assert counts.network_observed_attempt_count == 0
    assert counts.non_attempt_receipt_count == 2
    assert counts.attempt_receipt_count == 6
    assert counts.provider_response_receipt_count == 3
    assert counts.response_validation_count == 3
    assert counts.outer_diagnostic_count == 1

    ordered_states = [mixed_execution.states[item.plan.plan_id] for item in campaign.units]
    assert [state.transport.calls for state in ordered_states if state.transport is not None] == [
        1,
        1,
        1,
        1,
        1,
        0,
        0,
        1,
    ]
    assert ordered_states[5].egress.calls == 1
    assert ordered_states[5].credential.configured_checks == 1
    assert ordered_states[5].budget.calls == 1
    assert ordered_states[6].egress.calls == 1
    assert ordered_states[6].credential.configured_checks == 0
    assert ordered_states[6].budget.calls == 0
    assert all(state.credential.key_reads == 0 for state in ordered_states)
    assert all(
        key == "synthetic-injected-transport-no-provider-credential"
        for state in ordered_states
        if state.transport is not None
        for key in state.transport.seen_api_keys
    )

    durable_json = result.model_dump_json()
    assert _CREDENTIAL_SECRET not in durable_json
    assert _MODEL_REASON_SECRET not in durable_json
    assert _RAW_ERROR_SECRET not in durable_json
    assert "console.info" not in durable_json
    assert _MODEL_REASON_SECRET not in repr(mixed_execution.execution)
    assert all(
        _MODEL_REASON_SECRET not in repr(item) for item in mixed_execution.execution.unit_evidence
    )


def test_reversed_runtime_mapping_produces_the_same_canonical_result(
    campaign_setup: _CampaignSetup,
    mixed_execution: _MixedExecution,
) -> None:
    states = _mixed_states(campaign_setup)
    bindings = dict(reversed(tuple(_bindings(states).items())))
    rerun = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=bindings,
        limits=campaign_setup.limits,
        allow_injected_transport=True,
    )

    assert rerun.result == mixed_execution.execution.result
    assert tuple(item.plan_id for item in rerun.unit_evidence) == tuple(
        item.plan.plan_id for item in campaign_setup.trusted_upstream.bundle.units
    )


def test_unexpected_injected_transport_exception_is_redacted_and_campaign_continues(
    campaign_setup: _CampaignSetup,
) -> None:
    states = _mixed_states(campaign_setup)
    first_state = next(iter(states.values()))
    assert first_state.transport is not None
    first_state.transport.outcome = RuntimeError(_RAW_ERROR_SECRET)

    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=campaign_setup.limits,
        allow_injected_transport=True,
    )

    assert execution.result.units[0].attempt_outcome == "provider_transport_error"
    assert execution.result.units[-1].attempt_outcome == "valid_shape"
    assert _RAW_ERROR_SECRET not in execution.result.model_dump_json()
    assert _RAW_ERROR_SECRET not in repr(execution)


def test_full_raw_rebuild_accepts_exact_bytes_and_rejects_wrong_or_wrong_cover(
    mixed_execution: _MixedExecution,
) -> None:
    execution = mixed_execution.execution
    upstream = mixed_execution.setup.trusted_upstream
    evidence = {item.plan_id: item for item in execution.unit_evidence}
    raw = _raw_response_mapping(mixed_execution)

    verify_ai_tag_shadow_campaign_execution_result(
        execution.result,
        trusted_upstream=upstream,
        expected_limits=mixed_execution.setup.limits,
        evidence_by_plan_id=evidence,
        raw_response_body_by_plan_id=raw,
    )

    wrong_bytes = dict(raw)
    first_plan_id = next(iter(wrong_bytes))
    wrong_bytes[first_plan_id] = b"{}"
    with pytest.raises(ValueError):
        verify_ai_tag_shadow_campaign_execution_result(
            execution.result,
            trusted_upstream=upstream,
            expected_limits=mixed_execution.setup.limits,
            evidence_by_plan_id=evidence,
            raw_response_body_by_plan_id=wrong_bytes,
        )

    missing = dict(raw)
    missing.pop(first_plan_id)
    with pytest.raises(ValueError, match="exactly cover"):
        verify_ai_tag_shadow_campaign_execution_result(
            execution.result,
            trusted_upstream=upstream,
            expected_limits=mixed_execution.setup.limits,
            evidence_by_plan_id=evidence,
            raw_response_body_by_plan_id=missing,
        )

    extra = dict(raw)
    extra[_hash_id("ai-tag-shadow-plan", "extra-raw")] = b"{}"
    with pytest.raises(ValueError, match="exactly cover"):
        verify_ai_tag_shadow_campaign_execution_result(
            execution.result,
            trusted_upstream=upstream,
            expected_limits=mixed_execution.setup.limits,
            evidence_by_plan_id=evidence,
            raw_response_body_by_plan_id=extra,
        )


def _replace_result_unit(
    result: AITagShadowCampaignExecutionResult,
    index: int,
    unit: AITagShadowCampaignUnitExecution,
) -> AITagShadowCampaignExecutionResult:
    payload = result.model_dump(mode="json", exclude={"execution_result_id"})
    units = cast(list[dict[str, object]], payload["units"])
    units[index] = unit.model_dump(mode="json")
    payload["units"] = units
    return seal_ai_tag_shadow_campaign_execution_result(payload)


def test_result_reference_and_counter_tampering_is_rejected(
    mixed_execution: _MixedExecution,
) -> None:
    result = mixed_execution.execution.result
    result_identity_tamper = result.model_dump(mode="json")
    result_identity_tamper["execution_result_id"] = _hash_id(
        "ai-tag-shadow-campaign-execution-result",
        "forged-result",
    )
    with pytest.raises(ValueError, match="Result ID does not match"):
        load_ai_tag_shadow_campaign_execution_result(
            json.dumps(result_identity_tamper, separators=(",", ":"), sort_keys=True)
        )

    original_unit = result.units[0]
    unit_payload = original_unit.model_dump(mode="json", exclude={"unit_execution_id"})
    unit_payload["attempt_receipt_id"] = _hash_id("ai-tag-attempt-receipt", "forged")
    forged_unit = seal_ai_tag_shadow_campaign_unit_execution(unit_payload)
    forged_result = _replace_result_unit(result, 0, forged_unit)

    with pytest.raises(ValueError):
        verify_ai_tag_shadow_campaign_execution_result(
            forged_result,
            trusted_upstream=mixed_execution.setup.trusted_upstream,
            expected_limits=mixed_execution.setup.limits,
            evidence_by_plan_id={
                item.plan_id: item for item in mixed_execution.execution.unit_evidence
            },
        )

    zero_attempt = result.units[6]
    zero_payload = zero_attempt.model_dump(mode="json", exclude={"unit_execution_id"})
    zero_payload["local_non_attempt_reason"] = "credential_not_configured"
    forged_zero_attempt = seal_ai_tag_shadow_campaign_unit_execution(zero_payload)
    forged_zero_result = _replace_result_unit(result, 6, forged_zero_attempt)
    with pytest.raises(ValueError, match="artifact rebuild"):
        verify_ai_tag_shadow_campaign_execution_result(
            forged_zero_result,
            trusted_upstream=mixed_execution.setup.trusted_upstream,
            expected_limits=mixed_execution.setup.limits,
            evidence_by_plan_id={
                item.plan_id: item for item in mixed_execution.execution.unit_evidence
            },
        )

    result_payload = result.model_dump(mode="json", exclude={"execution_result_id"})
    counts = cast(dict[str, object], result_payload["counts"])
    counts["attempted_unit_count"] = cast(int, counts["attempted_unit_count"]) + 1
    result_payload["counts"] = counts
    with pytest.raises(ValueError, match="counts do not rebuild"):
        seal_ai_tag_shadow_campaign_execution_result(result_payload)


@pytest.mark.parametrize("mapping_change", ["missing", "extra"])
def test_runtime_binding_mapping_must_be_exact_before_any_runtime_effect(
    campaign_setup: _CampaignSetup,
    mapping_change: str,
) -> None:
    states = _mixed_states(campaign_setup)
    bindings = _bindings(states)
    if mapping_change == "missing":
        bindings.pop(next(iter(bindings)))
    else:
        bindings[_hash_id("ai-tag-shadow-plan", "extra-runtime")] = next(iter(bindings.values()))

    with pytest.raises(ValueError, match="exactly cover"):
        AITagShadowCampaignLiveHarness().execute(
            trusted_upstream=campaign_setup.trusted_upstream,
            runtime_bindings_by_plan_id=bindings,
            limits=campaign_setup.limits,
        )

    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())
    assert all(
        state.transport is not None and state.transport.calls == 0 for state in states.values()
    )


def test_credential_scope_mutation_after_gate_construction_does_not_change_binding(
    campaign_setup: _CampaignSetup,
) -> None:
    baseline_states = _mixed_states(campaign_setup)
    baseline = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(baseline_states),
        limits=campaign_setup.limits,
        allow_injected_transport=True,
    )
    states = _mixed_states(campaign_setup)
    last_state = next(reversed(states.values()))
    last_state.credential.marker = "credential-scope-drift"

    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=campaign_setup.limits,
        allow_injected_transport=True,
    )

    assert execution.result == baseline.result


def test_live_credential_disappearing_after_authorize_is_recorded_without_network(
    campaign_setup: _CampaignSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: dict[str, _RuntimeState] = {}
    for index, unit in enumerate(campaign_setup.trusted_upstream.bundle.units):
        state = _runtime_state(
            campaign_setup,
            unit,
            index=index,
            outcome=DeepSeekHttpResponse(
                200,
                _provider_body(unit, response_marker=f"credential-toctou-{index}"),
                None,
                1,
            ),
            include_transport=False,
        )
        state.credential.fail_on_get = True
        states[unit.plan.plan_id] = state

    network_calls = 0

    def forbidden_send(
        self: object,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        del self, plan, api_key
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("network transport must not run without a credential")

    monkeypatch.setattr(deepseek_adapter._HttpxDeepSeekShadowTransport, "send", forbidden_send)
    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=campaign_setup.limits,
        allow_live_transport=True,
    )

    assert network_calls == 0
    assert execution.result.counts.attempted_unit_count == 0
    assert execution.result.counts.not_run_unit_count == 8
    assert all(
        item.local_non_attempt_reason == "credential_not_configured"
        for item in execution.result.units
    )
    assert all(state.egress.calls == 1 for state in states.values())
    assert all(state.budget.calls == 1 for state in states.values())
    assert all(state.credential.configured_checks == 1 for state in states.values())
    assert all(state.credential.key_reads == 1 for state in states.values())


def test_default_live_transport_denial_preflights_before_credentials_or_network(
    campaign_setup: _CampaignSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: dict[str, _RuntimeState] = {}
    for index, unit in enumerate(campaign_setup.trusted_upstream.bundle.units):
        outcome = DeepSeekHttpResponse(
            200,
            _provider_body(unit, response_marker=f"deny-live-{index}"),
            None,
            1,
        )
        states[unit.plan.plan_id] = _runtime_state(
            campaign_setup,
            unit,
            index=index,
            outcome=outcome,
            include_transport=False,
        )

    network_calls = 0

    def forbidden_send(
        self: object,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        del self, plan, api_key
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("network transport must not run")

    monkeypatch.setattr(deepseek_adapter._HttpxDeepSeekShadowTransport, "send", forbidden_send)
    with pytest.raises(ValueError, match="allow_live_transport=True"):
        AITagShadowCampaignLiveHarness().execute(
            trusted_upstream=campaign_setup.trusted_upstream,
            runtime_bindings_by_plan_id=_bindings(states),
            limits=campaign_setup.limits,
        )

    assert network_calls == 0
    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())


def test_default_injected_transport_denial_preflights_before_runtime_effects(
    campaign_setup: _CampaignSetup,
) -> None:
    states = _mixed_states(campaign_setup)

    with pytest.raises(ValueError, match="allow_injected_transport=True"):
        AITagShadowCampaignLiveHarness().execute(
            trusted_upstream=campaign_setup.trusted_upstream,
            runtime_bindings_by_plan_id=_bindings(states),
            limits=campaign_setup.limits,
        )

    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())
    assert all(
        state.transport is not None and state.transport.calls == 0 for state in states.values()
    )


def test_externally_supplied_exact_httpx_transport_requires_injected_allow_flag(
    campaign_setup: _CampaignSetup,
) -> None:
    states = _mixed_states(campaign_setup)
    bindings = {
        plan_id: AITagShadowCampaignRuntimeBinding(
            claims=state.binding.claims,
            gate=state.binding.gate,
            transport=deepseek_adapter._HttpxDeepSeekShadowTransport(),  # noqa: SLF001
        )
        for plan_id, state in states.items()
    }

    with pytest.raises(ValueError, match="allow_injected_transport=True"):
        AITagShadowCampaignLiveHarness().execute(
            trusted_upstream=campaign_setup.trusted_upstream,
            runtime_bindings_by_plan_id=bindings,
            limits=campaign_setup.limits,
            allow_live_transport=True,
        )

    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())


def test_live_transport_process_preflight_occurs_before_any_authorization_effect(
    campaign_setup: _CampaignSetup,
) -> None:
    states: dict[str, _RuntimeState] = {}
    for index, unit in enumerate(campaign_setup.trusted_upstream.bundle.units):
        states[unit.plan.plan_id] = _runtime_state(
            campaign_setup,
            unit,
            index=index,
            outcome=DeepSeekHttpResponse(503, b"{}", None, 1),
            include_transport=False,
        )

    async def execute_inside_active_loop() -> None:
        AITagShadowCampaignLiveHarness().execute(
            trusted_upstream=campaign_setup.trusted_upstream,
            runtime_bindings_by_plan_id=_bindings(states),
            limits=campaign_setup.limits,
            allow_live_transport=True,
        )

    with pytest.raises(RuntimeError, match="active event loop"):
        asyncio.run(execute_inside_active_loop())

    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())


def test_campaign_deadline_can_stop_all_units_before_runtime_controls(
    campaign_setup: _CampaignSetup,
) -> None:
    states = _mixed_states(campaign_setup)
    limits_payload = campaign_setup.limits.model_dump(mode="json")
    limits_payload["campaign_wall_clock_cap_ms"] = 1_000
    limits = AITagShadowCampaignExecutionLimits.model_validate(limits_payload)

    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=limits,
        allow_injected_transport=True,
    )

    assert execution.result.counts.attempted_unit_count == 0
    assert execution.result.counts.not_run_unit_count == 8
    assert execution.result.counts.non_attempt_receipt_count == 8
    assert all(
        item.local_non_attempt_reason == "campaign_wall_clock_budget_insufficient"
        for item in execution.result.units
    )
    assert all(state.egress.calls == 0 for state in states.values())
    assert all(state.budget.calls == 0 for state in states.values())
    assert all(state.credential.configured_checks == 0 for state in states.values())
    assert all(state.credential.key_reads == 0 for state in states.values())
    assert all(
        state.transport is not None and state.transport.calls == 0 for state in states.values()
    )


def test_campaign_deadline_is_rechecked_after_authorization_before_dispatch(
    campaign_setup: _CampaignSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = _mixed_states(campaign_setup)
    limits_payload = campaign_setup.limits.model_dump(mode="json")
    limits_payload["campaign_wall_clock_cap_ms"] = 60_000
    limits = AITagShadowCampaignExecutionLimits.model_validate(limits_payload)
    clock_calls = 0

    def advancing_clock() -> int:
        nonlocal clock_calls
        clock_calls += 1
        return 0 if clock_calls <= 2 else 61_000_000_000

    monkeypatch.setattr(
        "arkts_code_reviewer.hybrid_analysis.shadow_campaign_execution.time.monotonic_ns",
        advancing_clock,
    )
    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=campaign_setup.trusted_upstream,
        runtime_bindings_by_plan_id=_bindings(states),
        limits=limits,
        allow_injected_transport=True,
    )

    assert execution.result.counts.attempted_unit_count == 0
    assert execution.result.counts.not_run_unit_count == 8
    first_receipt = execution.unit_evidence[0].non_attempt_receipt
    assert first_receipt is not None
    assert first_receipt.observed_control_stage == "campaign_deadline_post_authorization"
    assert all(
        state.transport is not None and state.transport.calls == 0 for state in states.values()
    )
    first_state = next(iter(states.values()))
    assert first_state.egress.calls == 1
    assert first_state.budget.calls == 1
    assert all(state.egress.calls == 0 for state in tuple(states.values())[1:])
    assert all(state.budget.calls == 0 for state in tuple(states.values())[1:])


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("max_units", 65),
        ("max_total_wire_body_bytes", 64_000_001),
        ("max_total_output_tokens", 262_145),
        ("max_total_response_bytes", 128_000_001),
        ("campaign_wall_clock_cap_ms", 3_600_001),
    ],
)
def test_campaign_execution_limits_have_small_hard_schema_ceilings(
    field_name: str,
    unsafe_value: int,
) -> None:
    payload: dict[str, int] = {
        "max_units": 1,
        "max_total_wire_body_bytes": 1,
        "max_total_output_tokens": 256,
        "max_total_response_bytes": 1_024,
        "campaign_wall_clock_cap_ms": 1_000,
    }
    payload[field_name] = unsafe_value
    with pytest.raises(ValueError):
        AITagShadowCampaignExecutionLimits.model_validate(payload)


def test_strict_loaders_round_trip_and_reject_duplicate_nonfinite_and_extra_fields(
    mixed_execution: _MixedExecution,
) -> None:
    result = mixed_execution.execution.result
    result_json = result.model_dump_json()
    unit = result.units[0]
    unit_json = unit.model_dump_json()
    non_attempt_receipt = next(
        item.non_attempt_receipt
        for item in mixed_execution.execution.unit_evidence
        if item.non_attempt_receipt is not None
    )
    assert isinstance(non_attempt_receipt, AITagShadowCampaignNonAttemptReceipt)
    receipt_json = non_attempt_receipt.model_dump_json()

    assert load_ai_tag_shadow_campaign_execution_result(result_json) == result
    assert load_ai_tag_shadow_campaign_unit_execution(unit_json) == unit
    assert load_ai_tag_shadow_campaign_non_attempt_receipt(receipt_json) == non_attempt_receipt
    assert (
        seal_ai_tag_shadow_campaign_non_attempt_receipt(
            non_attempt_receipt.model_dump(mode="json", exclude={"receipt_id"})
        )
        == non_attempt_receipt
    )

    duplicate = result_json[:-1] + ',"schema_version":"duplicate"}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_ai_tag_shadow_campaign_execution_result(duplicate)

    nonfinite = result_json.replace(
        f'"max_units":{result.execution_limits.max_units}',
        '"max_units":NaN',
        1,
    )
    with pytest.raises(ValueError, match="non-finite"):
        load_ai_tag_shadow_campaign_execution_result(nonfinite)

    extra_payload = json.loads(result_json)
    extra_payload["unexpected_field"] = "must-fail-closed"
    with pytest.raises(ValueError):
        load_ai_tag_shadow_campaign_execution_result(
            json.dumps(extra_payload, separators=(",", ":"), sort_keys=True)
        )

    duplicate_unit = unit_json[:-1] + ',"schema_version":"duplicate"}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_ai_tag_shadow_campaign_unit_execution(duplicate_unit)

    duplicate_receipt = receipt_json[:-1] + ',"schema_version":"duplicate"}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_ai_tag_shadow_campaign_non_attempt_receipt(duplicate_receipt)
