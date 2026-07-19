from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import cast

import pytest

from arkts_code_reviewer.hybrid_analysis.campaign_live_smoke import (
    RepositorySyntheticCampaignBundle,
    build_repository_synthetic_campaign_bundle,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
    DeepSeekShadowHttpTransport,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign_execution import (
    AITagShadowCampaignExecutionBundle,
    AITagShadowCampaignExecutionLimits,
    AITagShadowCampaignExecutionResult,
    AITagShadowCampaignLiveHarness,
    AITagShadowCampaignRuntimeBinding,
    AITagShadowCampaignUnitEvidence,
    seal_ai_tag_shadow_campaign_execution_result,
    verify_ai_tag_shadow_campaign_execution_result,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
    AITagShadowTrustedPlanInputs,
)


def _hash_id(prefix: str, marker: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{marker}".encode()).hexdigest()
    return f"{prefix}:sha256:{digest}"


@dataclass(frozen=True)
class _Credential:
    credential_scope_id: str

    def is_configured(self) -> bool:
        return True

    def get_api_key(self) -> str:
        return "unused-injected-transport-credential"


@dataclass(frozen=True)
class _AllowEgress:
    expected_plan_id: str

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert approval_id.startswith("ai-egress-approval:sha256:")


@dataclass(frozen=True)
class _AllowBudget:
    expected_plan_id: str

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert reservation_id.startswith("ai-budget-reservation:sha256:")


@dataclass(frozen=True)
class _TimeoutTransport:
    expected_plan_id: str

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.plan_id == self.expected_plan_id
        assert api_key == "synthetic-injected-transport-no-provider-credential"
        raise DeepSeekHttpTransportError("provider_timeout", latency_ms=1)


@dataclass(frozen=True)
class _CompletedCampaign:
    bundle: RepositorySyntheticCampaignBundle
    execution: AITagShadowCampaignExecutionBundle


def _claims(
    *,
    plan: AITagShadowDispatchPlan,
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


def _runtime_bindings(
    bundle: RepositorySyntheticCampaignBundle,
) -> dict[str, AITagShadowCampaignRuntimeBinding]:
    bindings: dict[str, AITagShadowCampaignRuntimeBinding] = {}
    for index, unit in enumerate(bundle.campaign.units):
        marker = f"limits-regression-{index}"
        credential = _Credential(_hash_id("deepseek-credential-scope", marker))
        claims = _claims(
            plan=unit.plan,
            credential_scope_id=credential.credential_scope_id,
            marker=marker,
        )
        gate = AITagShadowAuthorizationGate(
            trust_domain_id=claims.trust_domain_id,
            credential_provider=credential,
            trusted_plan_inputs=AITagShadowTrustedPlanInputs(
                envelope=unit.envelope,
                card=unit.card,
                context_policy=bundle.trusted_upstream.context_policy,
                max_output_tokens=unit.plan.wire_payload.max_tokens,
                wall_clock_timeout_ms=unit.plan.wall_clock_timeout_ms,
                max_response_bytes=unit.plan.max_response_bytes,
            ),
            egress_verifier=_AllowEgress(unit.plan.plan_id),
            budget_ledger=_AllowBudget(unit.plan.plan_id),
        )
        transport = cast(
            DeepSeekShadowHttpTransport,
            _TimeoutTransport(unit.plan.plan_id),
        )
        bindings[unit.plan.plan_id] = AITagShadowCampaignRuntimeBinding(
            claims=claims,
            gate=gate,
            transport=transport,
        )
    return bindings


@pytest.fixture(scope="module")
def completed_campaign() -> _CompletedCampaign:
    bundle = build_repository_synthetic_campaign_bundle()
    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=bundle.trusted_upstream,
        runtime_bindings_by_plan_id=_runtime_bindings(bundle),
        limits=bundle.caps.execution_limits(),
        allow_injected_transport=True,
    )
    assert execution.result.counts.attempted_unit_count > 0
    assert any(unit.plan.wall_clock_timeout_ms > 1_000 for unit in bundle.campaign.units)
    return _CompletedCampaign(bundle=bundle, execution=execution)


def _forged_result(
    completed: _CompletedCampaign,
    *,
    limits: AITagShadowCampaignExecutionLimits,
) -> AITagShadowCampaignExecutionResult:
    payload = completed.execution.result.model_dump(
        mode="json",
        exclude={"execution_result_id"},
    )
    payload["execution_limits"] = limits.model_dump(mode="json")
    return seal_ai_tag_shadow_campaign_execution_result(payload)


def _evidence_by_plan_id(
    completed: _CompletedCampaign,
) -> dict[str, AITagShadowCampaignUnitEvidence]:
    return {item.plan_id: item for item in completed.execution.unit_evidence}


def test_verifier_rejects_self_sealed_widened_limits(
    completed_campaign: _CompletedCampaign,
) -> None:
    approved = completed_campaign.bundle.caps.execution_limits()
    widened_payload = approved.model_dump(mode="json")
    widened_payload["max_total_wire_body_bytes"] += 1
    widened = AITagShadowCampaignExecutionLimits.model_validate(widened_payload)
    forged = _forged_result(completed_campaign, limits=widened)

    with pytest.raises(ValueError, match="expected execution limits"):
        verify_ai_tag_shadow_campaign_execution_result(
            forged,
            trusted_upstream=completed_campaign.bundle.trusted_upstream,
            expected_limits=approved,
            evidence_by_plan_id=_evidence_by_plan_id(completed_campaign),
        )


def test_verifier_rejects_attempt_under_forged_one_second_wall_clock_cap(
    completed_campaign: _CompletedCampaign,
) -> None:
    one_second_payload = completed_campaign.bundle.caps.execution_limits().model_dump(mode="json")
    one_second_payload["campaign_wall_clock_cap_ms"] = 1_000
    one_second = AITagShadowCampaignExecutionLimits.model_validate(one_second_payload)
    forged = _forged_result(completed_campaign, limits=one_second)

    with pytest.raises(ValueError, match="Plan timeout exceeds"):
        verify_ai_tag_shadow_campaign_execution_result(
            forged,
            trusted_upstream=completed_campaign.bundle.trusted_upstream,
            expected_limits=one_second,
            evidence_by_plan_id=_evidence_by_plan_id(completed_campaign),
        )
