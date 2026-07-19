from __future__ import annotations

import asyncio
from dataclasses import dataclass

from test_hybrid_analysis_dispatch import _artifacts

import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.hybrid_analysis.builders import AnalysisContextPolicy
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import DeepSeekHttpResponse
from arkts_code_reviewer.hybrid_analysis.models import seal_review_unit_analysis_card
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    build_ai_tag_shadow_dispatch_plan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
)


def _hash_id(prefix: str, marker: str) -> str:
    return f"{prefix}:sha256:{marker * 64}"


def _plan() -> tuple[object, object, AITagShadowDispatchPlan, AnalysisContextPolicy]:
    default_card, _, _, _, _ = _artifacts()
    policy = AnalysisContextPolicy(
        builder_version="analysis-card-builder-v2-provider-egress",
        redaction_policy="none_requires_exact_body_runtime_approval",
    )
    card_payload = default_card.model_dump(mode="json", exclude={"card_id"})
    card_payload["context_policy_fingerprint"] = policy.fingerprint
    card = seal_review_unit_analysis_card(card_payload)
    _, _, _, envelope, _ = _artifacts(card=card)
    plan = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=4_096,
    )
    return card, envelope, plan, policy


def _claims(
    plan: AITagShadowDispatchPlan,
    *,
    credential_scope_id: str,
) -> AITagShadowDispatchClaims:
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": plan.plan_id,
            "trust_domain_id": _hash_id("ai-shadow-trust-domain", "a"),
            "egress_approval_id": _hash_id("ai-egress-approval", "b"),
            "budget_reservation_id": _hash_id("ai-budget-reservation", "c"),
            "credential_scope_id": credential_scope_id,
            "egress_scope": "exact_wire_body_sha256",
            "budget_scope": "one_attempt_worst_case_reserved",
            "qualification": "references_require_runtime_verification",
        }
    )


@dataclass
class _Credential:
    scope_id: str = _hash_id("deepseek-credential-scope", "d")
    secret: str = "test-provider-secret"
    configured: bool = True
    scope_reads: int = 0
    api_key_reads: int = 0

    @property
    def credential_scope_id(self) -> str:
        self.scope_reads += 1
        return self.scope_id

    def is_configured(self) -> bool:
        return self.configured

    def get_api_key(self) -> str:
        self.api_key_reads += 1
        return self.secret


@dataclass
class _ChangingScopeCredential(_Credential):
    later_scope_id: str = _hash_id("deepseek-credential-scope", "e")

    @property
    def credential_scope_id(self) -> str:
        self.scope_reads += 1
        return self.scope_id if self.scope_reads == 1 else self.later_scope_id


class _AllowEgress:
    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        assert plan.wire_body_sha256.startswith("sha256:")
        assert approval_id.startswith("ai-egress-approval:sha256:")


class _AllowBudget:
    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        assert plan.max_attempts == 1
        assert reservation_id.startswith("ai-budget-reservation:sha256:")


def _runtime(
    *,
    credential: _Credential,
) -> tuple[
    object,
    object,
    AITagShadowDispatchPlan,
    AITagShadowDispatchClaims,
    AITagShadowAuthorizationGate,
]:
    card, envelope, plan, policy = _plan()
    claims = _claims(plan, credential_scope_id=credential.scope_id)
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=AITagShadowTrustedPlanInputs(
            envelope=envelope,  # type: ignore[arg-type]
            card=card,  # type: ignore[arg-type]
            context_policy=policy,
            max_output_tokens=4_096,
            wall_clock_timeout_ms=60_000,
            max_response_bytes=2_000_000,
        ),
        egress_verifier=_AllowEgress(),
        budget_ledger=_AllowBudget(),
    )
    return card, envelope, plan, claims, gate


@dataclass
class _AuthorizationShapedFailureTransport:
    calls: int = 0

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.max_attempts == 1
        assert api_key.startswith("synthetic-injected-transport-")
        self.calls += 1
        raise AITagShadowAuthorizationError("credential_not_configured")


def test_transport_origin_authorization_error_is_an_attempted_transport_failure() -> None:
    credential = _Credential()
    _, envelope, plan, claims, gate = _runtime(credential=credential)
    capability = gate.authorize(plan=plan, claims=claims)
    transport = _AuthorizationShapedFailureTransport()
    runner = DeepSeekShadowRunner(gate=gate, transport=transport)

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,  # type: ignore[arg-type]
    )

    assert transport.calls == 1
    assert artifacts.attempt_receipt.transport_status == "provider_transport_error"
    assert artifacts.attempt_receipt.transport_evidence == "injected_untrusted_transport"
    assert artifacts.attempt_receipt.network_observation == (
        "not_established_by_injected_transport"
    )
    assert credential.api_key_reads == 0

    # A genuine Gate replay denial still propagates and never re-enters send().
    try:
        runner.run(
            plan=plan,
            claims=claims,
            capability=capability,
            envelope=envelope,  # type: ignore[arg-type]
        )
    except AITagShadowAuthorizationError as exc:
        assert exc.reason_code == "capability_replayed"
    else:
        raise AssertionError("replayed capability was accepted")
    assert transport.calls == 1


def test_gate_snapshots_credential_scope_once_for_claims_and_capability_binding() -> None:
    credential = _ChangingScopeCredential()
    _, envelope, plan, claims, gate = _runtime(credential=credential)
    capability = gate.authorize(plan=plan, claims=claims)

    class _ResponseTransport:
        def send(
            self,
            sent_plan: AITagShadowDispatchPlan,
            *,
            api_key: str,
        ) -> DeepSeekHttpResponse:
            assert sent_plan == plan
            assert api_key.startswith("synthetic-injected-transport-")
            return DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 1)

    artifacts = DeepSeekShadowRunner(gate=gate, transport=_ResponseTransport()).run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,  # type: ignore[arg-type]
    )

    assert credential.scope_reads == 1
    assert credential.api_key_reads == 0
    assert artifacts.attempt_receipt.credential_scope_id == credential.scope_id


def test_externally_supplied_exact_httpx_instance_is_untrusted_and_gets_no_secret() -> None:
    credential = _Credential(secret="must-never-reach-external-transport")
    _, envelope, plan, claims, gate = _runtime(credential=credential)
    capability = gate.authorize(plan=plan, claims=claims)
    transport = deepseek_adapter._HttpxDeepSeekShadowTransport()  # noqa: SLF001
    seen_keys: list[str] = []

    def external_send(
        sent_plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert sent_plan == plan
        seen_keys.append(api_key)
        return DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 1)

    transport.send = external_send  # type: ignore[assignment]
    artifacts = DeepSeekShadowRunner(gate=gate, transport=transport).run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,  # type: ignore[arg-type]
    )

    assert seen_keys == ["synthetic-injected-transport-no-provider-credential"]
    assert credential.secret not in seen_keys
    assert credential.api_key_reads == 0
    assert artifacts.attempt_receipt.transport_evidence == "injected_untrusted_transport"
    assert artifacts.attempt_receipt.network_observation == (
        "not_established_by_injected_transport"
    )


def test_active_event_loop_fails_before_capability_consumption_or_network_observation() -> None:
    credential = _Credential()
    _, envelope, plan, claims, gate = _runtime(credential=credential)
    capability = gate.authorize(plan=plan, claims=claims)
    runner = DeepSeekShadowRunner(gate=gate)

    async def invoke_sync_runner() -> None:
        runner.run(
            plan=plan,
            claims=claims,
            capability=capability,
            envelope=envelope,  # type: ignore[arg-type]
        )

    try:
        asyncio.run(invoke_sync_runner())
    except RuntimeError as exc:
        assert str(exc) == "synchronous DeepSeek transport cannot run inside an active event loop"
    else:
        raise AssertionError("synchronous runner was accepted inside an active event loop")

    assert credential.api_key_reads == 0
    # Preflight happens before dispatch, so the still-unused capability can be revoked.
    gate.revoke_unused_capability(capability=capability, plan=plan, claims=claims)
