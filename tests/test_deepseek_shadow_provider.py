from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field, replace

import httpx
import pytest
from test_hybrid_analysis_dispatch import _artifacts, _judgments, _wire_content

import arkts_code_reviewer.hybrid_analysis as hybrid_analysis
import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.hybrid_analysis.builders import AnalysisContextPolicy
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
    DeepSeekOuterResponseError,
    EnvironmentDeepSeekCredentialProvider,
    parse_deepseek_chat_completion,
    verify_deepseek_observed_provider_response_receipt,
)
from arkts_code_reviewer.hybrid_analysis.execution import seal_ai_tag_response_validation
from arkts_code_reviewer.hybrid_analysis.models import (
    AITagAnalysisResult,
    AITagExecutionOutcome,
    HybridFeatureAnalysisResult,
    seal_review_unit_analysis_card,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    build_ai_tag_shadow_dispatch_plan,
    load_ai_tag_dispatch_attempt_receipt,
    load_ai_tag_observed_provider_response_receipt,
    load_ai_tag_shadow_dispatch_claims,
    load_ai_tag_shadow_dispatch_plan,
    load_ai_tag_shadow_execution_observation,
    seal_ai_tag_dispatch_attempt_receipt,
    seal_ai_tag_observed_provider_response_receipt,
    seal_ai_tag_shadow_dispatch_claims,
    seal_ai_tag_shadow_dispatch_plan,
    seal_ai_tag_shadow_execution_observation,
    verify_ai_tag_dispatch_attempt_receipt,
    verify_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowDispatchCapability,
    AITagShadowRunArtifacts,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    verify_deepseek_shadow_run_artifacts as _verify_deepseek_shadow_run_artifacts,
)


def _hash_id(prefix: str, marker: str) -> str:
    return f"{prefix}:sha256:{marker * 64}"


def _egress_policy() -> AnalysisContextPolicy:
    return AnalysisContextPolicy(
        builder_version="analysis-card-builder-v2-provider-egress",
        redaction_policy="none_requires_exact_body_runtime_approval",
    )


def _plan():  # type: ignore[no-untyped-def]
    default_card, _, _, _, _ = _artifacts()
    policy = _egress_policy()
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


def _trusted_inputs(
    *,
    card,
    envelope,
    policy: AnalysisContextPolicy,
    max_output_tokens: int = 4_096,
    timeout_ms: int = 60_000,
    max_response_bytes: int = 2_000_000,
) -> AITagShadowTrustedPlanInputs:  # type: ignore[no-untyped-def]
    return AITagShadowTrustedPlanInputs(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=max_output_tokens,
        wall_clock_timeout_ms=timeout_ms,
        max_response_bytes=max_response_bytes,
    )


def _default_trusted_inputs(plan: AITagShadowDispatchPlan) -> AITagShadowTrustedPlanInputs:
    card, envelope, expected_plan, policy = _plan()
    if plan != expected_plan:
        raise AssertionError("non-default test Plan requires explicit trusted inputs")
    return _trusted_inputs(card=card, envelope=envelope, policy=policy)


def verify_deepseek_shadow_run_artifacts(  # type: ignore[no-untyped-def]
    artifacts,
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    envelope,
    raw_response_body: bytes | None,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs | None = None,
) -> None:
    roots = _default_trusted_inputs(plan) if trusted_plan_inputs is None else trusted_plan_inputs
    assert roots.envelope == envelope
    _verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        trusted_plan_inputs=roots,
        raw_response_body=raw_response_body,
    )


def _claims(
    plan: AITagShadowDispatchPlan,
    *,
    credential_scope_id: str,
    trust_marker: str = "a",
) -> AITagShadowDispatchClaims:
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": plan.plan_id,
            "trust_domain_id": _hash_id("ai-shadow-trust-domain", trust_marker),
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
    secret: str = "test-deepseek-secret"
    configured: bool = True
    events: list[str] = field(default_factory=list)

    @property
    def credential_scope_id(self) -> str:
        return _hash_id("deepseek-credential-scope", "d")

    def is_configured(self) -> bool:
        self.events.append("credential")
        return self.configured

    def get_api_key(self) -> str:
        self.events.append("get_api_key")
        return self.secret


@dataclass
class _EgressVerifier:
    events: list[str]
    deny: bool = False

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        assert approval_id.startswith("ai-egress-approval:sha256:")
        assert plan.wire_body_sha256.startswith("sha256:")
        self.events.append("egress")
        if self.deny:
            raise AITagShadowAuthorizationError("egress_not_approved")


@dataclass
class _BudgetLedger:
    events: list[str]
    deny: bool = False

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        assert reservation_id.startswith("ai-budget-reservation:sha256:")
        assert plan.max_attempts == 1
        self.events.append("budget")
        if self.deny:
            raise AITagShadowAuthorizationError("budget_not_reserved")


class _ScriptedTransport:
    def __init__(
        self,
        outcome: DeepSeekHttpResponse | DeepSeekHttpTransportError,
    ) -> None:
        self.outcome = outcome
        self.calls = 0
        self.seen_keys: list[str] = []

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.max_attempts == 1
        self.calls += 1
        self.seen_keys.append(api_key)
        if isinstance(self.outcome, DeepSeekHttpTransportError):
            raise self.outcome
        return self.outcome


def _authorized_runner(
    *,
    plan: AITagShadowDispatchPlan,
    transport: object,
    credential: _Credential | EnvironmentDeepSeekCredentialProvider | None = None,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs | None = None,
):  # type: ignore[no-untyped-def]
    events: list[str] = []
    credential = _Credential() if credential is None else credential
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=(
            _default_trusted_inputs(plan) if trusted_plan_inputs is None else trusted_plan_inputs
        ),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    capability = gate.authorize(
        plan=plan,
        claims=claims,
    )
    runner = DeepSeekShadowRunner(
        gate=gate,
        transport=transport,  # type: ignore[arg-type]
    )
    return runner, capability, gate, claims


def _provider_body(envelope, *, content: str | None = None, **updates: object) -> bytes:  # type: ignore[no-untyped-def]
    if content is None:
        content = _wire_content(_judgments(envelope))
    payload: dict[str, object] = {
        "id": "chatcmpl-shadow-test",
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
        "system_fingerprint": "fp-shadow-test",
        "usage": {
            "completion_tokens": 50,
            "prompt_tokens": 100,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 25,
            "prompt_cache_miss_tokens": 75,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    payload.update(updates)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_shadow_plan_is_deterministic_canonical_and_adds_only_bounded_output() -> None:
    card, envelope, first, policy = _plan()
    second = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=4_096,
    )
    different_budget = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=8_192,
    )

    assert first == second
    assert first.plan_id == second.plan_id
    assert first.plan_id != different_budget.plan_id
    assert first.wire_body_sha256 != different_budget.wire_body_sha256
    assert first.max_attempts == 1
    assert first.wall_clock_timeout_ms == 60_000
    assert first.execution_mode == "shadow_only_no_hybrid_no_retrieval"
    assert first.qualification == "plan_not_authorization"
    assert first.tls_verify is True
    assert first.follow_redirects is False
    assert first.trust_env is False
    assert first.shadow_provider_policy.policy_version == ("deepseek-shadow-provider-policy-v2")
    assert first.shadow_provider_policy.upstream_render_policy_fingerprint == (
        envelope.model_policy.model_policy_fingerprint
    )
    assert first.shadow_provider_policy.upstream_dispatch_mode_required == (
        "disabled_no_budget_no_approval"
    )
    assert first.shadow_provider_policy.max_output_tokens == 4_096
    assert first.shadow_provider_policy.wall_clock_timeout_ms == 60_000
    assert first.shadow_provider_policy.retry_policy == "none_single_attempt_v1"
    assert first.shadow_provider_policy.qualification == (
        "development_shadow_not_production_approved"
    )

    wire = json.loads(first.wire_body_json)
    base_wire = json.loads(envelope.wire_body_json)
    assert wire == {**base_wire, "max_tokens": 4_096}
    assert first.wire_body_json == json.dumps(
        first.wire_payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert first.wire_body_sha256 == (
        "sha256:" + hashlib.sha256(first.wire_body_json.encode()).hexdigest()
    )
    assert load_ai_tag_shadow_dispatch_plan(first.model_dump_json()) == first
    verify_ai_tag_shadow_dispatch_plan(
        first,
        envelope=envelope,
        card=card,
        context_policy=policy,
        trusted_max_output_tokens=4_096,
        trusted_timeout_ms=60_000,
        trusted_max_response_bytes=2_000_000,
    )
    with pytest.raises(ValueError, match="deterministic rebuild"):
        verify_ai_tag_shadow_dispatch_plan(
            different_budget,
            envelope=envelope,
            card=card,
            context_policy=policy,
            trusted_max_output_tokens=4_096,
            trusted_timeout_ms=60_000,
            trusted_max_response_bytes=2_000_000,
        )

    with pytest.raises(ValueError, match="greater than or equal to 256"):
        build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=policy,
            max_output_tokens=255,
        )
    with pytest.raises(ValueError, match="less than or equal to 16384"):
        build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=policy,
            max_output_tokens=16_385,
        )

    policy_unbound = first.model_dump(mode="json", exclude={"plan_id"})
    policy_unbound["wall_clock_timeout_ms"] = 61_000
    with pytest.raises(ValueError, match="provider policy"):
        seal_ai_tag_shadow_dispatch_plan(policy_unbound)


def test_default_v1_card_policy_cannot_be_promoted_to_a_send_plan() -> None:
    fixture_card, _, _, _, _ = _artifacts()
    default_policy = AnalysisContextPolicy()
    card_payload = fixture_card.model_dump(mode="json", exclude={"card_id"})
    card_payload["context_policy_fingerprint"] = default_policy.fingerprint
    card = seal_review_unit_analysis_card(card_payload)
    _, _, _, envelope, _ = _artifacts(card=card)

    with pytest.raises(ValueError, match="does not permit provider egress"):
        build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=default_policy,
            max_output_tokens=4_096,
        )

    with pytest.raises(ValueError, match="supplied context policy"):
        build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=_egress_policy(),
            max_output_tokens=4_096,
        )

    with pytest.raises(ValueError, match="does not permit provider egress"):
        AITagShadowTrustedPlanInputs(
            envelope=envelope,
            card=card,
            context_policy=default_policy,
            max_output_tokens=4_096,
            wall_clock_timeout_ms=60_000,
            max_response_bytes=2_000_000,
        )


def test_serializable_claims_are_not_authority_and_default_gate_denies() -> None:
    _, _, plan, _ = _plan()
    credential = _Credential()
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
    )

    assert claims.qualification == "references_require_runtime_verification"
    assert load_ai_tag_shadow_dispatch_claims(claims.model_dump_json()) == claims
    assert not isinstance(claims, AITagShadowDispatchCapability)
    with pytest.raises(AITagShadowAuthorizationError) as raised:
        gate.authorize(
            plan=plan,
            claims=claims,
        )
    assert raised.value.reason_code == "egress_not_approved"

    with pytest.raises(
        TypeError,
        match="capabilities can only be issued by an authorization gate",
    ):
        AITagShadowDispatchCapability(
            construction_token=object(),
            gate_nonce="forged",
            plan_id=plan.plan_id,
            trust_domain_id=claims.trust_domain_id,
            credential_scope_id=claims.credential_scope_id,
            claims_id=claims.claims_id,
        )


def test_gate_rejects_plan_limits_not_frozen_by_its_trusted_roots() -> None:
    card, envelope, _, policy = _plan()
    untrusted_plan = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=8_192,
    )
    credential = _Credential()
    claims = _claims(
        untrusted_plan,
        credential_scope_id=credential.credential_scope_id,
    )
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=_trusted_inputs(
            card=card,
            envelope=envelope,
            policy=policy,
            max_output_tokens=4_096,
        ),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )

    with pytest.raises(AITagShadowAuthorizationError) as rejected:
        gate.authorize(plan=untrusted_plan, claims=claims)

    assert rejected.value.reason_code == "plan_not_trusted"
    assert events == []
    assert credential.events == []


def test_authorization_gate_is_fail_closed_in_a_stable_order() -> None:
    _, _, plan, _ = _plan()
    base_credential = _Credential()
    claims = _claims(
        plan,
        credential_scope_id=base_credential.credential_scope_id,
    )

    events: list[str] = []
    mismatch_credential = _Credential(events=events)
    mismatch_gate = AITagShadowAuthorizationGate(
        trust_domain_id=_hash_id("ai-shadow-trust-domain", "d"),
        credential_provider=mismatch_credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    with pytest.raises(AITagShadowAuthorizationError) as mismatch:
        mismatch_gate.authorize(
            plan=plan,
            claims=claims,
        )
    assert mismatch.value.reason_code == "claims_mismatch"
    assert events == []

    events = []
    scope_credential = _Credential(events=events)
    wrong_scope_claims = _claims(
        plan,
        credential_scope_id=_hash_id("deepseek-credential-scope", "e"),
    )
    scope_gate = AITagShadowAuthorizationGate(
        trust_domain_id=wrong_scope_claims.trust_domain_id,
        credential_provider=scope_credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    with pytest.raises(AITagShadowAuthorizationError) as scope_mismatch:
        scope_gate.authorize(plan=plan, claims=wrong_scope_claims)
    assert scope_mismatch.value.reason_code == "claims_mismatch"
    assert events == []

    events = []
    egress_credential = _Credential(events=events)
    egress_gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=egress_credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events, deny=True),
        budget_ledger=_BudgetLedger(events),
    )
    with pytest.raises(AITagShadowAuthorizationError) as egress:
        egress_gate.authorize(
            plan=plan,
            claims=claims,
        )
    assert egress.value.reason_code == "egress_not_approved"
    assert events == ["egress"]

    events = []
    unavailable_credential = _Credential(configured=False, events=events)
    credential_gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=unavailable_credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    with pytest.raises(AITagShadowAuthorizationError) as credential:
        credential_gate.authorize(
            plan=plan,
            claims=claims,
        )
    assert credential.value.reason_code == "credential_not_configured"
    assert events == ["egress", "credential"]

    events = []
    budget_credential = _Credential(events=events)
    budget_gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=budget_credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events, deny=True),
    )
    with pytest.raises(AITagShadowAuthorizationError) as budget:
        budget_gate.authorize(
            plan=plan,
            claims=claims,
        )
    assert budget.value.reason_code == "budget_not_reserved"
    assert events == ["egress", "credential", "budget"]


def test_capability_is_opaque_non_serializable_and_single_use() -> None:
    _, envelope, plan, _ = _plan()
    transport = _ScriptedTransport(
        DeepSeekHttpResponse(
            status_code=503,
            body=b'{"error":"unavailable"}',
            retry_after_ms=None,
            latency_ms=7,
        )
    )
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )

    assert repr(capability) == "AITagShadowDispatchCapability(<opaque-single-use>)"
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(capability)

    first = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    assert first.observation.status == "provider_server_error"
    assert transport.calls == 1

    with pytest.raises(AITagShadowAuthorizationError) as replayed:
        runner.run(
            plan=plan,
            claims=claims,
            capability=capability,
            envelope=envelope,
        )
    assert replayed.value.reason_code == "capability_replayed"
    assert transport.calls == 1


def test_gate_has_no_plan_only_real_credential_send_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, plan, _ = _plan()
    credential = _Credential()
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    send_calls: list[tuple[str, str]] = []

    def fixed_send(
        _transport: object,
        sent_plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        send_calls.append((sent_plan.plan_id, api_key))
        return DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 1)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    transport = deepseek_adapter._HttpxDeepSeekShadowTransport()  # noqa: SLF001

    assert not hasattr(gate, "_send_with_fixed_transport")
    assert not hasattr(gate, "consume")
    with pytest.raises(AITagShadowAuthorizationError) as rejected:
        gate.dispatch_once(
            capability=object(),  # type: ignore[arg-type]
            plan=plan,
            claims=claims,
            transport=transport,
        )

    assert rejected.value.reason_code == "capability_invalid"
    assert credential.events == []
    assert send_calls == []


def test_gate_dispatch_once_binds_claims_then_consumes_capability_before_key_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, plan, _ = _plan()
    credential = _Credential(secret="fixed-transport-secret")
    authorized_claims = _claims(
        plan,
        credential_scope_id=credential.credential_scope_id,
    )
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=authorized_claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=_default_trusted_inputs(plan),
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    capability = gate.authorize(plan=plan, claims=authorized_claims)
    send_calls: list[tuple[str, str]] = []

    def fixed_send(
        _transport: object,
        sent_plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        send_calls.append((sent_plan.plan_id, api_key))
        return DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 1)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    transport = deepseek_adapter._HttpxDeepSeekShadowTransport()  # noqa: SLF001
    swapped_payload = authorized_claims.model_dump(mode="json", exclude={"claims_id"})
    swapped_payload["egress_approval_id"] = _hash_id("ai-egress-approval", "e")
    swapped_claims = seal_ai_tag_shadow_dispatch_claims(swapped_payload)

    with pytest.raises(AITagShadowAuthorizationError) as wrong_binding:
        gate.dispatch_once(
            capability=capability,
            plan=plan,
            claims=swapped_claims,
            transport=transport,
        )
    assert wrong_binding.value.reason_code == "capability_invalid"
    assert credential.events == ["credential"]
    assert send_calls == []

    response = gate.dispatch_once(
        capability=capability,
        plan=plan,
        claims=authorized_claims,
        transport=transport,
    )
    assert response.status_code == 503
    assert credential.events == ["credential", "get_api_key"]
    assert send_calls == [(plan.plan_id, "fixed-transport-secret")]

    with pytest.raises(AITagShadowAuthorizationError) as replayed:
        gate.dispatch_once(
            capability=capability,
            plan=plan,
            claims=authorized_claims,
            transport=transport,
        )
    assert replayed.value.reason_code == "capability_replayed"
    assert credential.events == ["credential", "get_api_key"]
    assert send_calls == [(plan.plan_id, "fixed-transport-secret")]


def test_capability_cannot_be_reused_with_different_self_hashed_claims() -> None:
    _, envelope, plan, _ = _plan()
    transport = _ScriptedTransport(DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 7))
    runner, capability, _, authorized_claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )
    swapped_payload = authorized_claims.model_dump(
        mode="json",
        exclude={"claims_id"},
    )
    swapped_payload["egress_approval_id"] = _hash_id("ai-egress-approval", "e")
    swapped_claims = seal_ai_tag_shadow_dispatch_claims(swapped_payload)

    with pytest.raises(AITagShadowAuthorizationError) as rejected:
        runner.run(
            plan=plan,
            claims=swapped_claims,
            capability=capability,
            envelope=envelope,
        )

    assert rejected.value.reason_code == "capability_invalid"
    assert transport.calls == 0


def test_provider_outer_parser_accepts_the_frozen_non_thinking_contract() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)

    parsed = parse_deepseek_chat_completion(
        raw_body,
        plan=plan,
        latency_ms=37,
    )

    assert parsed.response.id == "chatcmpl-shadow-test"
    assert parsed.response.model == "deepseek-v4-pro"
    assert parsed.response.choices[0].message.reasoning_content is None
    assert parsed.raw_completion.source_kind == "unverified_raw"
    assert parsed.raw_completion.content == _wire_content(_judgments(envelope))
    assert parsed.raw_completion.model == "deepseek-v4-pro"
    assert parsed.raw_completion.finish_reason == "stop"
    assert parsed.raw_completion.latency_ms == 37
    assert parsed.raw_completion.attempt_count == 1
    assert parsed.raw_completion.usage is not None
    assert parsed.raw_completion.usage.prompt_tokens == 100
    assert parsed.raw_completion.usage.completion_tokens == 50
    assert parsed.raw_completion.usage.prompt_cache_hit_tokens == 25


@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"id":"first","id":"duplicate"}',
        b'{"error":{"message":"bad request"}}',
        b"[]",
    ],
)
def test_provider_outer_parser_rejects_non_contract_json(raw_body: bytes) -> None:
    _, _, plan, _ = _plan()

    with pytest.raises(DeepSeekOuterResponseError, match="frozen provider contract"):
        parse_deepseek_chat_completion(raw_body, plan=plan, latency_ms=1)


def test_provider_outer_parser_rejects_wrong_model_reasoning_and_bad_usage() -> None:
    _, envelope, plan, _ = _plan()
    base = json.loads(_provider_body(envelope))
    variants: list[dict[str, object]] = []

    wrong_model = json.loads(json.dumps(base))
    wrong_model["model"] = "deepseek-chat"
    variants.append(wrong_model)

    reasoning = json.loads(json.dumps(base))
    reasoning["choices"][0]["message"]["reasoning_content"] = "hidden chain"  # type: ignore[index]
    variants.append(reasoning)

    bad_usage = json.loads(json.dumps(base))
    bad_usage["usage"]["total_tokens"] = 149  # type: ignore[index]
    variants.append(bad_usage)

    for payload in variants:
        with pytest.raises(DeepSeekOuterResponseError):
            parse_deepseek_chat_completion(
                json.dumps(payload, separators=(",", ":")).encode(),
                plan=plan,
                latency_ms=1,
            )


def test_valid_http_200_produces_receipts_and_only_non_formal_validation() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)
    secret = "test-deepseek-secret"
    transport = _ScriptedTransport(
        DeepSeekHttpResponse(
            status_code=200,
            body=raw_body,
            retry_after_ms=None,
            latency_ms=37,
        )
    )
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
        credential=_Credential(secret=secret),
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert transport.calls == 1
    assert len(transport.seen_keys) == 1
    assert transport.seen_keys[0].startswith("synthetic-injected-transport-")
    assert secret not in transport.seen_keys
    assert artifacts.attempt_receipt.transport_status == "response_received"
    assert artifacts.attempt_receipt.http_status == 200
    assert artifacts.attempt_receipt.response_body_sha256 == (
        "sha256:" + hashlib.sha256(raw_body).hexdigest()
    )
    assert artifacts.attempt_receipt.transport_evidence == ("injected_untrusted_transport")
    assert artifacts.attempt_receipt.network_observation == (
        "not_established_by_injected_transport"
    )
    assert artifacts.attempt_receipt.qualification == (
        "synthetic_or_untrusted_transport_not_network_evidence"
    )
    assert artifacts.provider_response_receipt is not None
    assert artifacts.provider_response_receipt.provider_response_id == ("chatcmpl-shadow-test")
    assert artifacts.provider_response_receipt.qualification == (
        "synthetic_or_untrusted_transport_not_provider_observation"
    )
    assert artifacts.response_validation is not None
    assert artifacts.response_validation.status == "valid_shape"
    assert artifacts.response_validation.qualification == ("synthetic_or_unattributed_not_formal")
    assert artifacts.observation.status == "valid_shape"
    assert artifacts.observation.qualification == "unattested_shadow_not_formal"

    verify_ai_tag_dispatch_attempt_receipt(
        artifacts.attempt_receipt,
        plan=plan,
        claims=claims,
    )
    verify_deepseek_observed_provider_response_receipt(
        artifacts.provider_response_receipt,
        plan=plan,
        attempt_receipt=artifacts.attempt_receipt,
        raw_body=raw_body,
    )
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=raw_body,
    )

    assert (
        load_ai_tag_dispatch_attempt_receipt(artifacts.attempt_receipt.model_dump_json())
        == artifacts.attempt_receipt
    )
    assert (
        load_ai_tag_observed_provider_response_receipt(
            artifacts.provider_response_receipt.model_dump_json()
        )
        == artifacts.provider_response_receipt
    )
    assert (
        load_ai_tag_shadow_execution_observation(artifacts.observation.model_dump_json())
        == artifacts.observation
    )

    for artifact in (
        artifacts.attempt_receipt,
        artifacts.provider_response_receipt,
        artifacts.response_validation,
        artifacts.observation,
    ):
        assert not isinstance(
            artifact,
            AITagAnalysisResult | AITagExecutionOutcome | HybridFeatureAnalysisResult,
        )

    receipt_json = "".join(
        (
            artifacts.attempt_receipt.model_dump_json(),
            artifacts.provider_response_receipt.model_dump_json(),
            artifacts.observation.model_dump_json(),
        )
    )
    assert secret not in receipt_json
    assert envelope.model_view.code.numbered_text not in receipt_json
    assert _wire_content(_judgments(envelope)) not in receipt_json


def test_http_200_with_valid_outer_but_invalid_inner_stays_non_formal() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope, content='{"judgments":[]}')
    transport = _ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 11))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert transport.calls == 1
    assert artifacts.provider_response_receipt is not None
    assert artifacts.response_validation is not None
    assert artifacts.response_validation.status == "invalid_output"
    assert artifacts.observation.status == "invalid_output"
    assert artifacts.observation.provider_response_receipt_id == (
        artifacts.provider_response_receipt.receipt_id
    )
    assert artifacts.observation.response_validation_id == (
        artifacts.response_validation.validation_id
    )
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=raw_body,
    )
    assert not isinstance(
        artifacts.response_validation,
        AITagAnalysisResult | AITagExecutionOutcome | HybridFeatureAnalysisResult,
    )


def test_http_200_with_invalid_outer_has_attempt_only_and_no_claimed_response() -> None:
    _, envelope, plan, _ = _plan()
    transport = _ScriptedTransport(
        DeepSeekHttpResponse(200, b'{"error":"not a completion"}', None, 9)
    )
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert transport.calls == 1
    assert artifacts.attempt_receipt.http_status == 200
    assert artifacts.provider_response_receipt is None
    assert artifacts.response_validation is None
    assert artifacts.observation.status == "invalid_output"
    assert artifacts.observation.reason_code == "provider_outer_contract_invalid"
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=b'{"error":"not a completion"}',
    )


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_http_status"),
    [
        (
            DeepSeekHttpResponse(429, b'{"error":"rate limited"}', 2_000, 5),
            "provider_rate_limited",
            429,
        ),
        (
            DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 6),
            "provider_server_error",
            503,
        ),
        (
            DeepSeekHttpTransportError("provider_timeout", latency_ms=7),
            "provider_timeout",
            None,
        ),
    ],
)
def test_rate_limit_server_error_and_timeout_never_retry(
    outcome: DeepSeekHttpResponse | DeepSeekHttpTransportError,
    expected_status: str,
    expected_http_status: int | None,
) -> None:
    _, envelope, plan, _ = _plan()
    transport = _ScriptedTransport(outcome)
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert plan.max_attempts == 1
    assert transport.calls == 1
    assert artifacts.attempt_receipt.attempt_ordinal == 1
    assert artifacts.attempt_receipt.http_status == expected_http_status
    assert artifacts.attempt_receipt.transport_evidence == ("injected_untrusted_transport")
    assert artifacts.attempt_receipt.qualification == (
        "synthetic_or_untrusted_transport_not_network_evidence"
    )
    assert artifacts.provider_response_receipt is None
    assert artifacts.response_validation is None
    assert artifacts.observation.status == expected_status
    assert artifacts.observation.reason_code == expected_status
    verify_ai_tag_dispatch_attempt_receipt(
        artifacts.attempt_receipt,
        plan=plan,
        claims=claims,
    )
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=(outcome.body if isinstance(outcome, DeepSeekHttpResponse) else None),
    )


def test_injected_httpx_transport_never_receives_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, envelope, plan, _ = _plan()
    secret = "ds-test-secret-never-persist"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    credential = EnvironmentDeepSeekCredentialProvider()
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["content_type"] = request.headers["Content-Type"]
        seen["body"] = request.content
        return httpx.Response(
            429,
            headers={"Retry-After": "3"},
            content=b'{"error":"rate limited"}',
        )

    transport = deepseek_adapter._HttpxDeepSeekShadowTransport(  # noqa: SLF001
        http_transport=httpx.MockTransport(handle)
    )
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
        credential=credential,
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert seen == {
        "authorization": "Bearer synthetic-injected-transport-no-provider-credential",
        "content_type": "application/json",
        "body": plan.wire_body_json.encode(),
    }
    assert artifacts.attempt_receipt.http_status == 429
    assert artifacts.attempt_receipt.retry_after_ms == 3_000
    assert artifacts.attempt_receipt.transport_evidence == ("injected_untrusted_transport")
    assert artifacts.attempt_receipt.qualification == (
        "synthetic_or_untrusted_transport_not_network_evidence"
    )
    assert artifacts.attempt_receipt.network_observation == (
        "not_established_by_injected_transport"
    )
    assert artifacts.observation.status == "provider_rate_limited"
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=b'{"error":"rate limited"}',
    )
    assert "_HttpxDeepSeekShadowTransport" not in deepseek_adapter.__all__
    assert not hasattr(hybrid_analysis, "_HttpxDeepSeekShadowTransport")
    serialized = "".join(
        (
            plan.model_dump_json(),
            claims.model_dump_json(),
            artifacts.attempt_receipt.model_dump_json(),
            artifacts.observation.model_dump_json(),
            repr(capability),
            repr(credential),
            repr(transport),
        )
    )
    assert secret not in serialized


def test_environment_credential_name_is_strictly_uppercase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("deepseek_api_key", "lowercase-name-must-not-be-accepted")

    credential = EnvironmentDeepSeekCredentialProvider()

    assert credential.is_configured() is False


def test_observed_response_receipt_rejects_different_raw_bytes() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)
    transport = _ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 3))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )
    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    assert artifacts.provider_response_receipt is not None

    with pytest.raises(ValueError, match="does not bind"):
        verify_deepseek_observed_provider_response_receipt(
            artifacts.provider_response_receipt,
            plan=plan,
            attempt_receipt=artifacts.attempt_receipt,
            raw_body=raw_body + b" ",
        )


def test_semantically_tampered_self_hashed_response_receipt_is_rejected() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)
    transport = _ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 3))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )
    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    assert artifacts.provider_response_receipt is not None

    receipt_payload = artifacts.provider_response_receipt.model_dump(
        mode="json",
        exclude={"receipt_id"},
    )
    receipt_payload["created"] = receipt_payload["created"] + 1  # type: ignore[operator]
    tampered_receipt = seal_ai_tag_observed_provider_response_receipt(receipt_payload)
    observation_payload = artifacts.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload["provider_response_receipt_id"] = tampered_receipt.receipt_id
    tampered_artifacts = replace(
        artifacts,
        provider_response_receipt=tampered_receipt,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="trusted raw-response rebuild"):
        verify_deepseek_shadow_run_artifacts(
            tampered_artifacts,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=raw_body,
        )


def test_http_status_tamper_is_rejected_by_complete_rebuild() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = b'{"error":"rate limited"}'
    transport = _ScriptedTransport(DeepSeekHttpResponse(429, raw_body, 2_000, 5))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )
    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    observation_payload = artifacts.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload.update(
        {
            "status": "provider_server_error",
            "reason_code": "provider_server_error",
        }
    )
    tampered_artifacts = replace(
        artifacts,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="HTTP failure observation"):
        verify_deepseek_shadow_run_artifacts(
            tampered_artifacts,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=raw_body,
        )


def test_cross_response_validation_is_rejected_even_when_each_artifact_is_valid() -> None:
    _, envelope, plan, _ = _plan()
    first_body = _provider_body(envelope)
    second_body = _provider_body(
        envelope,
        content=_wire_content(_judgments(envelope, decision="abstain")),
    )

    first_runner, first_capability, _, claims = _authorized_runner(
        plan=plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, first_body, None, 3)),
    )
    first = first_runner.run(
        plan=plan,
        claims=claims,
        capability=first_capability,
        envelope=envelope,
    )
    second_runner, second_capability, _, second_claims = _authorized_runner(
        plan=plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, second_body, None, 4)),
    )
    second = second_runner.run(
        plan=plan,
        claims=second_claims,
        capability=second_capability,
        envelope=envelope,
    )
    assert first.response_validation is not None
    assert second.response_validation is not None
    assert first.response_validation.validation_id != second.response_validation.validation_id

    observation_payload = first.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload["response_validation_id"] = second.response_validation.validation_id
    crossed = replace(
        first,
        response_validation=second.response_validation,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="inner validation differs"):
        verify_deepseek_shadow_run_artifacts(
            crossed,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=first_body,
        )


def test_self_hashed_judgment_tamper_is_rejected_by_raw_response_rebuild() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 3)),
    )
    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    assert artifacts.response_validation is not None

    validation_payload = artifacts.response_validation.model_dump(
        mode="json",
        exclude={"validation_id"},
    )
    judgments = validation_payload["judgments"]
    assert isinstance(judgments, list)
    assert isinstance(judgments[0], dict)
    judgments[0].update(
        {
            "decision": "abstain",
            "reason_code": "insufficient_context",
            "reason": "self-hashed but not present in the raw response",
        }
    )
    tampered_validation = seal_ai_tag_response_validation(validation_payload)
    observation_payload = artifacts.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload["response_validation_id"] = tampered_validation.validation_id
    tampered_artifacts = replace(
        artifacts,
        response_validation=tampered_validation,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="trusted raw-response rebuild"):
        verify_deepseek_shadow_run_artifacts(
            tampered_artifacts,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=raw_body,
        )


def test_cross_envelope_validation_is_rejected_before_status_can_be_trusted() -> None:
    first_card, first_envelope, first_plan, policy = _plan()
    first_body = _provider_body(first_envelope)
    first_runner, first_capability, _, first_claims = _authorized_runner(
        plan=first_plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, first_body, None, 3)),
    )
    first = first_runner.run(
        plan=first_plan,
        claims=first_claims,
        capability=first_capability,
        envelope=first_envelope,
    )

    second_card_payload = first_card.model_dump(mode="json", exclude={"card_id"})
    second_code = dict(second_card_payload["code"])  # type: ignore[arg-type]
    second_code["text"] = "load() {\n  this.refreshAgain();\n}"
    second_card_payload["code"] = second_code
    second_card = seal_review_unit_analysis_card(second_card_payload)
    _, _, _, second_envelope, _ = _artifacts(card=second_card)
    second_plan = build_ai_tag_shadow_dispatch_plan(
        envelope=second_envelope,
        card=second_card,
        context_policy=policy,
        max_output_tokens=4_096,
    )
    second_body = _provider_body(second_envelope)
    second_runner, second_capability, _, second_claims = _authorized_runner(
        plan=second_plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, second_body, None, 4)),
        trusted_plan_inputs=_trusted_inputs(
            card=second_card,
            envelope=second_envelope,
            policy=policy,
        ),
    )
    second = second_runner.run(
        plan=second_plan,
        claims=second_claims,
        capability=second_capability,
        envelope=second_envelope,
    )
    assert second.response_validation is not None

    observation_payload = first.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload["response_validation_id"] = second.response_validation.validation_id
    crossed = replace(
        first,
        response_validation=second.response_validation,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="does not reference its envelope"):
        verify_deepseek_shadow_run_artifacts(
            crossed,
            plan=first_plan,
            claims=first_claims,
            envelope=first_envelope,
            raw_response_body=first_body,
        )


def test_success_observation_cannot_be_relabelled_as_invalid_output() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(envelope)
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 3)),
    )
    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    observation_payload = artifacts.observation.model_dump(
        mode="json",
        exclude={"observation_id"},
    )
    observation_payload.update(
        {
            "status": "invalid_output",
            "reason_code": "judgment_set_incomplete",
        }
    )
    relabelled = replace(
        artifacts,
        observation=seal_ai_tag_shadow_execution_observation(observation_payload),
    )

    with pytest.raises(ValueError, match="inner validation differs"):
        verify_deepseek_shadow_run_artifacts(
            relabelled,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=raw_body,
        )


def test_injected_transport_body_over_plan_limit_becomes_failure_without_body_claim() -> None:
    card, envelope, _, policy = _plan()
    plan = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=4_096,
        max_response_bytes=1_024,
    )
    transport = _ScriptedTransport(DeepSeekHttpResponse(200, b"x" * 1_025, None, 8))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
        trusted_plan_inputs=_trusted_inputs(
            card=card,
            envelope=envelope,
            policy=policy,
            max_response_bytes=1_024,
        ),
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert transport.calls == 1
    assert artifacts.attempt_receipt.transport_status == ("provider_response_too_large")
    assert artifacts.attempt_receipt.http_status is None
    assert artifacts.attempt_receipt.response_body_sha256 is None
    assert artifacts.attempt_receipt.response_body_size_bytes is None
    assert artifacts.attempt_receipt.transport_evidence == ("injected_untrusted_transport")
    assert artifacts.provider_response_receipt is None
    assert artifacts.response_validation is None
    assert artifacts.observation.status == "provider_response_too_large"
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=None,
        trusted_plan_inputs=_trusted_inputs(
            card=card,
            envelope=envelope,
            policy=policy,
            max_response_bytes=1_024,
        ),
    )


def test_self_hashed_response_receipt_cannot_exceed_frozen_plan_budget() -> None:
    card, envelope, _, policy = _plan()
    plan = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=policy,
        max_output_tokens=4_096,
        max_response_bytes=1_024,
    )
    credential = _Credential()
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    raw_body = b"x" * 1_025
    attempt = seal_ai_tag_dispatch_attempt_receipt(
        {
            "schema_version": "ai-tag-dispatch-attempt-receipt-v1",
            "plan_id": plan.plan_id,
            "envelope_id": plan.envelope_id,
            "request_id": plan.request_id,
            "claims_id": claims.claims_id,
            "trust_domain_id": claims.trust_domain_id,
            "egress_approval_id": claims.egress_approval_id,
            "budget_reservation_id": claims.budget_reservation_id,
            "credential_scope_id": claims.credential_scope_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "endpoint_url": plan.endpoint_url,
            "http_method": plan.http_method,
            "attempt_ordinal": 1,
            "tls_verify": plan.tls_verify,
            "follow_redirects": plan.follow_redirects,
            "trust_env": plan.trust_env,
            "transport_evidence": "injected_untrusted_transport",
            "network_observation": "not_established_by_injected_transport",
            "transport_status": "response_received",
            "http_status": 429,
            "response_body_sha256": ("sha256:" + hashlib.sha256(raw_body).hexdigest()),
            "response_body_size_bytes": len(raw_body),
            "retry_after_ms": None,
            "latency_ms": 3,
            "qualification": ("synthetic_or_untrusted_transport_not_network_evidence"),
        }
    )
    observation = seal_ai_tag_shadow_execution_observation(
        {
            "schema_version": "ai-tag-shadow-execution-observation-v1",
            "plan_id": plan.plan_id,
            "claims_id": claims.claims_id,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": None,
            "response_validation_id": None,
            "status": "provider_rate_limited",
            "reason_code": "provider_rate_limited",
            "qualification": "unattested_shadow_not_formal",
        }
    )
    artifacts = AITagShadowRunArtifacts(
        attempt_receipt=attempt,
        provider_response_receipt=None,
        response_validation=None,
        observation=observation,
    )

    with pytest.raises(ValueError, match="frozen plan byte budget"):
        verify_deepseek_shadow_run_artifacts(
            artifacts,
            plan=plan,
            claims=claims,
            envelope=envelope,
            raw_response_body=raw_body,
            trusted_plan_inputs=_trusted_inputs(
                card=card,
                envelope=envelope,
                policy=policy,
                max_response_bytes=1_024,
            ),
        )


def test_partial_provider_cache_usage_does_not_invent_zeroes() -> None:
    _, envelope, plan, _ = _plan()
    raw_body = _provider_body(
        envelope,
        usage={
            "completion_tokens": 50,
            "prompt_tokens": 100,
            "total_tokens": 150,
        },
    )
    transport = _ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 6))
    runner, capability, _, claims = _authorized_runner(
        plan=plan,
        transport=transport,
    )

    artifacts = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert artifacts.provider_response_receipt is not None
    assert artifacts.provider_response_receipt.usage is not None
    assert artifacts.provider_response_receipt.usage.prompt_tokens == 100
    assert artifacts.provider_response_receipt.usage.completion_tokens == 50
    assert artifacts.provider_response_receipt.usage.prompt_cache_hit_tokens is None
    assert artifacts.response_validation is not None
    assert artifacts.response_validation.status == "valid_shape"
    assert artifacts.response_validation.usage.input_tokens is None
    assert artifacts.response_validation.usage.output_tokens is None
    assert artifacts.response_validation.usage.cache_read_input_tokens is None
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        envelope=envelope,
        raw_response_body=raw_body,
    )
