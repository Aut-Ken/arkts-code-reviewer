from __future__ import annotations

from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_deepseek_shadow_provider import (
    _BudgetLedger,
    _claims,
    _Credential,
    _EgressVerifier,
    _plan,
    _provider_body,
    _trusted_inputs,
)
from test_hybrid_formal_execution import (
    _PRIVATE_SEED,
    _REGISTRY_POLICY,
    _RUNNER_ID,
    _RUNNER_RELEASE,
    _trust_objects,
)

import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.hybrid_analysis._canonical import canonical_hash
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import VerifiedAITagDispatchEnvelope
from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AITagFormalExecutionEvidenceV2,
    AITagFormalExecutionVerifierV2,
    AITagTrustedRunnerRegistry,
    AITagTrustedRunnerSigner,
    DeepSeekFormalExecutionRunnerV2,
    build_ai_tag_trusted_runner_key_record,
    compute_ai_tag_runner_registry_id,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    build_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
    AITagShadowDispatchCapability,
)


@dataclass(frozen=True)
class _AttemptFixture:
    plan: AITagShadowDispatchPlan
    claims: AITagShadowDispatchClaims
    gate: AITagShadowAuthorizationGate
    capability: AITagShadowDispatchCapability
    envelope: VerifiedAITagDispatchEnvelope
    seen: list[tuple[str, str]]


def _transport_outcome(
    case: str,
    *,
    envelope: object,
) -> DeepSeekHttpResponse | DeepSeekHttpTransportError:
    if case == "valid":
        return DeepSeekHttpResponse(200, _provider_body(envelope), None, 7)
    if case == "inner_invalid":
        return DeepSeekHttpResponse(
            200,
            _provider_body(envelope, content='{"judgments":[]}'),
            None,
            8,
        )
    if case == "outer_invalid":
        return DeepSeekHttpResponse(200, b"{}", None, 9)
    if case == "http_429":
        return DeepSeekHttpResponse(429, b'{"error":"rate_limited"}', 25, 10)
    if case == "http_503":
        return DeepSeekHttpResponse(503, b'{"error":"unavailable"}', None, 11)
    if case == "timeout":
        return DeepSeekHttpTransportError("provider_timeout", latency_ms=12)
    if case == "transport_error":
        return DeepSeekHttpTransportError("provider_transport_error", latency_ms=13)
    if case == "response_too_large":
        return DeepSeekHttpResponse(200, b"x" * 1_025, None, 14)
    raise AssertionError(f"unknown Formal V2 matrix case: {case}")


def _attempt(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> _AttemptFixture:
    card, envelope, plan, policy = _plan()
    if case == "response_too_large":
        plan = build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=policy,
            max_output_tokens=4_096,
            max_response_bytes=1_024,
        )
        trusted_inputs = _trusted_inputs(
            card=card,
            envelope=envelope,
            policy=policy,
            max_response_bytes=1_024,
        )
    else:
        trusted_inputs = _trusted_inputs(
            card=card,
            envelope=envelope,
            policy=policy,
        )

    credential = _Credential(secret="formal-matrix-secret")
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=trusted_inputs,
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    capability = gate.authorize(plan=plan, claims=claims)
    outcome = _transport_outcome(case, envelope=envelope)
    seen: list[tuple[str, str]] = []

    def fixed_send(
        _transport: object,
        supplied_plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        seen.append((supplied_plan.plan_id, api_key))
        if isinstance(outcome, DeepSeekHttpTransportError):
            raise outcome
        return outcome

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    return _AttemptFixture(
        plan=plan,
        claims=claims,
        gate=gate,
        capability=capability,
        envelope=envelope,
        seen=seen,
    )


def _formalize(
    attempt: _AttemptFixture,
    *,
    signer: AITagTrustedRunnerSigner,
    registry: AITagTrustedRunnerRegistry,
) -> AITagFormalExecutionEvidenceV2:
    evidence = DeepSeekFormalExecutionRunnerV2(
        gate=attempt.gate,
        signer=signer,
        registry=registry,
    ).run(
        plan=attempt.plan,
        claims=attempt.claims,
        capability=attempt.capability,
        envelope=attempt.envelope,
    )
    assert attempt.seen == [(attempt.plan.plan_id, "formal-matrix-secret")]
    return evidence


@pytest.mark.parametrize(
    (
        "case",
        "expected_status",
        "expected_reason",
        "has_complete_response",
    ),
    [
        ("valid", "valid_result", "provider_response_valid", True),
        ("inner_invalid", "invalid_output", "incomplete_taxonomy", True),
        (
            "outer_invalid",
            "invalid_output",
            "provider_outer_contract_invalid",
            True,
        ),
        ("http_429", "unavailable", "provider_rate_limited", True),
        ("http_503", "unavailable", "provider_server_error", True),
        ("timeout", "unavailable", "provider_timeout", False),
        ("transport_error", "unavailable", "provider_transport_error", False),
        (
            "response_too_large",
            "unavailable",
            "provider_response_too_large",
            False,
        ),
    ],
)
def test_attempted_formal_v2_status_matrix_and_evidence_scopes(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_status: str,
    expected_reason: str,
    has_complete_response: bool,
) -> None:
    attempt = _attempt(monkeypatch, case)
    signer, registry = _trust_objects(attempt.claims.trust_domain_id)
    evidence = _formalize(attempt, signer=signer, registry=registry)
    bundle = evidence.bundle

    assert bundle.outcome.status == expected_status
    assert bundle.outcome.reason_code == expected_reason
    assert bundle.subject.formal_status == expected_status
    assert bundle.subject.reason_code == expected_reason
    assert (bundle.result is not None) is (case == "valid")
    assert (bundle.outcome.result_id is not None) is (case == "valid")
    assert (bundle.hybrid.ai_result_id is not None) is (case == "valid")

    expected_provider_scope = (
        "http_response_observed_over_tls_not_provider_signed"
        if has_complete_response
        else "fixed_tls_transport_attempt_no_complete_verified_response"
    )
    expected_raw_scope = (
        "passed_complete_http_response"
        if has_complete_response
        else "not_applicable_no_complete_http_response"
    )
    assert bundle.outcome.provider_evidence_scope == expected_provider_scope
    assert bundle.subject.provider_evidence_scope == expected_provider_scope
    assert bundle.subject.raw_response_rebuild_scope == expected_raw_scope
    assert (bundle.outcome.response_body_sha256 is not None) is has_complete_response
    assert (bundle.subject.response_body_sha256 is not None) is has_complete_response
    if bundle.result is not None:
        assert bundle.result.provider_evidence_scope == ("observed_over_tls_not_provider_signed")
    else:
        assert all(state.ai_unit_decision is None for state in bundle.hybrid.tag_states)

    eligibility = AITagFormalExecutionVerifierV2(registry=registry).verify(evidence)
    assert eligibility.hybrid == bundle.hybrid
    assert eligibility.positive_tags == ()


def test_contradictory_self_hashed_status_artifacts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_attempt = _attempt(monkeypatch, "valid")
    valid_signer, valid_registry = _trust_objects(valid_attempt.claims.trust_domain_id)
    valid = _formalize(
        valid_attempt,
        signer=valid_signer,
        registry=valid_registry,
    )
    outcome_payload = valid.bundle.outcome.model_dump(
        mode="json",
        exclude={"outcome_id"},
    )
    outcome_payload.update(
        {
            "status": "invalid_output",
            "result_id": None,
            "reason_code": "provider_outer_contract_invalid",
            "outer_diagnostic_id": f"deepseek-outer-response-diagnostic:sha256:{'8' * 64}",
        }
    )
    outcome_payload["outcome_id"] = canonical_hash(
        "ai-tag-outcome-v2",
        outcome_payload,
    )
    with pytest.raises(ValueError, match="either inner or outer diagnostics"):
        type(valid.bundle.outcome).model_validate(outcome_payload)

    timeout_attempt = _attempt(monkeypatch, "timeout")
    timeout_signer, timeout_registry = _trust_objects(timeout_attempt.claims.trust_domain_id)
    timeout = _formalize(
        timeout_attempt,
        signer=timeout_signer,
        registry=timeout_registry,
    )
    for updates in (
        {
            "response_body_sha256": f"sha256:{'7' * 64}",
            "response_body_size_bytes": 1,
            "provider_evidence_scope": ("http_response_observed_over_tls_not_provider_signed"),
        },
        {"reason_code": "provider_client_error"},
    ):
        payload = timeout.bundle.outcome.model_dump(
            mode="json",
            exclude={"outcome_id"},
        )
        payload.update(updates)
        payload["outcome_id"] = canonical_hash("ai-tag-outcome-v2", payload)
        with pytest.raises(ValueError, match="response presence differs from reason"):
            type(timeout.bundle.outcome).model_validate(payload)

    subject_payload = valid.bundle.subject.model_dump(
        mode="json",
        exclude={"subject_id"},
    )
    subject_payload.update(
        {
            "formal_status": "invalid_output",
            "result_id": None,
            "reason_code": "provider_outer_contract_invalid",
            "outer_diagnostic_id": f"deepseek-outer-response-diagnostic:sha256:{'8' * 64}",
        }
    )
    subject_payload["subject_id"] = canonical_hash(
        "ai-tag-trusted-execution-subject",
        subject_payload,
    )
    with pytest.raises(ValueError, match="invalid-output trusted subject"):
        type(valid.bundle.subject).model_validate(subject_payload)


def _wrong_release_trust_objects(
    trust_domain_id: str,
) -> tuple[AITagTrustedRunnerSigner, AITagTrustedRunnerRegistry]:
    private_key = Ed25519PrivateKey.from_private_bytes(_PRIVATE_SEED)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    wrong_release = f"ai-tag-runner-release:sha256:{'9' * 64}"
    record = build_ai_tag_trusted_runner_key_record(
        public_key_bytes=public_key,
        runner_id=_RUNNER_ID,
        trust_domain_id=trust_domain_id,
        allowed_runner_release_fingerprints=(wrong_release,),
    )
    registry_id = compute_ai_tag_runner_registry_id(
        trust_domain_id=trust_domain_id,
        registry_policy_fingerprint=_REGISTRY_POLICY,
        records=(record,),
    )
    registry = AITagTrustedRunnerRegistry(
        expected_registry_id=registry_id,
        trust_domain_id=trust_domain_id,
        registry_policy_fingerprint=_REGISTRY_POLICY,
        records=(record,),
    )
    signer = AITagTrustedRunnerSigner.from_private_key_bytes(
        private_key_bytes=_PRIVATE_SEED,
        trust_domain_id=trust_domain_id,
        runner_id=_RUNNER_ID,
        runner_release_fingerprint=_RUNNER_RELEASE,
        runner_registry_id=registry_id,
        registry_policy_fingerprint=_REGISTRY_POLICY,
    )
    return signer, registry


@pytest.mark.parametrize("trust_case", ["revoked_key", "wrong_release"])
def test_formal_producer_rejects_revoked_key_and_wrong_runner_release(
    monkeypatch: pytest.MonkeyPatch,
    trust_case: str,
) -> None:
    attempt = _attempt(monkeypatch, "valid")
    if trust_case == "revoked_key":
        signer, registry = _trust_objects(
            attempt.claims.trust_domain_id,
            revoked=True,
        )
        match = "revoked key"
    else:
        signer, registry = _wrong_release_trust_objects(
            attempt.claims.trust_domain_id,
        )
        match = "identity or release is not allowed"

    with pytest.raises(ValueError, match=match):
        _formalize(attempt, signer=signer, registry=registry)
    assert attempt.seen == []
