from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import replace

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
    _ScriptedTransport,
    _trusted_inputs,
)

import arkts_code_reviewer.hybrid_analysis as hybrid_analysis
import arkts_code_reviewer.hybrid_analysis.deepseek_adapter as deepseek_adapter
from arkts_code_reviewer.hybrid_analysis._canonical import canonical_hash
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import DeepSeekHttpResponse
from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AI_TAG_FORMALIZATION_POLICY_FINGERPRINT,
    AITagAnalysisResultV2,
    AITagFormalExecutionEvidenceV2,
    AITagFormalExecutionVerifierV2,
    AITagTrustedRunnerRegistry,
    AITagTrustedRunnerSigner,
    DeepSeekFormalExecutionRunnerV2,
    HybridFeatureAnalysisResultV2,
    VerifiedAITagFormalExecutionEligibility,
    build_ai_tag_trusted_runner_key_record,
    compute_ai_tag_runner_registry_id,
    load_ai_tag_analysis_result_v2,
    load_ai_tag_execution_outcome_v2,
    load_ai_tag_trusted_execution_subject,
    load_ai_tag_trusted_runner_attestation,
    load_hybrid_feature_analysis_result_v2,
)
from arkts_code_reviewer.hybrid_analysis.models import load_ai_tag_analysis_result
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
    DeepSeekShadowRunner,
)

_RUNNER_ID = f"ai-tag-runner:sha256:{'1' * 64}"
_RUNNER_RELEASE = f"ai-tag-runner-release:sha256:{'2' * 64}"
_REGISTRY_POLICY = f"ai-tag-runner-registry-policy:sha256:{'3' * 64}"
_PRIVATE_SEED = bytes(range(32))


def _trust_objects(trust_domain_id: str, *, revoked: bool = False):  # type: ignore[no-untyped-def]
    private_key = Ed25519PrivateKey.from_private_bytes(_PRIVATE_SEED)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    record = build_ai_tag_trusted_runner_key_record(
        public_key_bytes=public_key,
        runner_id=_RUNNER_ID,
        trust_domain_id=trust_domain_id,
        allowed_runner_release_fingerprints=(_RUNNER_RELEASE,),
        status="revoked" if revoked else "active",
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


def _fixed_run(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    card, envelope, plan, policy = _plan()
    raw_body = _provider_body(envelope)
    credential = _Credential(secret="formal-fixture-secret")
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    trusted_inputs = _trusted_inputs(
        card=card,
        envelope=envelope,
        policy=policy,
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
    seen: list[tuple[str, str]] = []

    def fixed_send(
        _transport: object,
        supplied_plan,  # type: ignore[no-untyped-def]
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        seen.append((supplied_plan.plan_id, api_key))
        return DeepSeekHttpResponse(200, raw_body, None, 7)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    signer, registry = _trust_objects(claims.trust_domain_id)
    evidence = DeepSeekFormalExecutionRunnerV2(
        gate=gate,
        signer=signer,
        registry=registry,
    ).run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    assert seen == [(plan.plan_id, "formal-fixture-secret")]
    return evidence, signer, registry


_UNCHANGED = object()


def _tampered_evidence(
    evidence: AITagFormalExecutionEvidenceV2,
    *,
    raw_response_body: bytes | None | object = _UNCHANGED,
    bundle: object = _UNCHANGED,
) -> AITagFormalExecutionEvidenceV2:
    """Simulate unsupported trusted-process memory tampering for verifier tests."""

    forged = object.__new__(AITagFormalExecutionEvidenceV2)
    for name in ("_claims", "_plan", "_run_artifacts", "_trusted_plan_inputs"):
        object.__setattr__(forged, name, object.__getattribute__(evidence, name))
    object.__setattr__(
        forged,
        "_raw_response_body",
        (
            object.__getattribute__(evidence, "_raw_response_body")
            if raw_response_body is _UNCHANGED
            else raw_response_body
        ),
    )
    object.__setattr__(
        forged,
        "_bundle",
        object.__getattribute__(evidence, "_bundle") if bundle is _UNCHANGED else bundle,
    )
    object.__setattr__(forged, "_runtime_sealed", True)
    return forged


def test_fixed_transport_raw_rebuild_signs_and_verifies_formal_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, signer, registry = _fixed_run(monkeypatch)
    bundle = evidence.bundle
    result = bundle.result
    assert result is not None
    assert result.schema_version == "ai-tag-analysis-result-v2"
    assert result.result_id.startswith("ai-tag-result-v2:sha256:")
    assert result.transport_evidence == "httpx_tls_fixed_endpoint"
    assert result.provider_evidence_scope == "observed_over_tls_not_provider_signed"
    assert result.evidence_qualification_status == "not_qualified"
    assert result.production_qualified is False
    assert len(result.judgments) == 24
    assert bundle.outcome.status == "valid_result"
    assert bundle.outcome.reason_code == "provider_response_valid"
    assert bundle.outcome.result_id == result.result_id
    assert bundle.subject.result_id == result.result_id
    assert bundle.subject.outcome_id == bundle.outcome.outcome_id
    assert bundle.subject.runner_key_id == signer.runner_key_id
    assert bundle.subject.runner_registry_id == registry.registry_id
    assert bundle.subject.formalization_policy_fingerprint == (
        AI_TAG_FORMALIZATION_POLICY_FINGERPRINT
    )
    assert bundle.attestation.subject_id == bundle.subject.subject_id
    assert bundle.attestation.signature_algorithm == "ed25519"
    assert len(bundle.attestation.signature_hex) == 128
    assert bundle.hybrid.ai_result_id == result.result_id
    assert bundle.hybrid.production_qualified is False

    eligibility = AITagFormalExecutionVerifierV2(registry=registry).verify(evidence)
    assert isinstance(eligibility, VerifiedAITagFormalExecutionEligibility)
    assert eligibility.unit_id == bundle.hybrid.unit_id
    assert eligibility.attestation_id == bundle.attestation.attestation_id
    assert eligibility.positive_tags == ()
    assert eligibility.hybrid == bundle.hybrid


def test_integrated_formal_runner_owns_raw_response_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card, envelope, plan, policy = _plan()
    raw_body = _provider_body(envelope)
    credential = _Credential(secret="integrated-formal-fixture-secret")
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    trusted_inputs = _trusted_inputs(card=card, envelope=envelope, policy=policy)
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=trusted_inputs,
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    capability = gate.authorize(plan=plan, claims=claims)
    seen: list[tuple[str, str]] = []

    def fixed_send(
        _transport: object,
        supplied_plan,  # type: ignore[no-untyped-def]
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        seen.append((supplied_plan.plan_id, api_key))
        return DeepSeekHttpResponse(200, raw_body, None, 7)

    monkeypatch.setattr(
        deepseek_adapter._HttpxDeepSeekShadowTransport,  # noqa: SLF001
        "send",
        fixed_send,
    )
    signer, registry = _trust_objects(claims.trust_domain_id)
    runner = DeepSeekFormalExecutionRunnerV2(
        gate=gate,
        signer=signer,
        registry=registry,
    )

    evidence = runner.run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )

    assert seen == [(plan.plan_id, "integrated-formal-fixture-secret")]
    assert evidence.bundle.outcome.response_body_sha256 == (
        "sha256:" + hashlib.sha256(raw_body).hexdigest()
    )
    assert not hasattr(evidence, "raw_response_body")
    assert evidence.bundle.result is not None
    assert AITagFormalExecutionVerifierV2(registry=registry).verify(evidence).hybrid == (
        evidence.bundle.hybrid
    )
    with pytest.raises(AttributeError, match="immutable"):
        runner._shadow_runner = object()  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        del runner._runtime_sealed  # noqa: SLF001
    with pytest.raises(TypeError):
        pickle.dumps(runner)


def test_formal_artifacts_have_strict_round_trip_and_v1_loader_separation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _, _ = _fixed_run(monkeypatch)
    bundle = evidence.bundle
    assert bundle.result is not None
    assert load_ai_tag_analysis_result_v2(bundle.result.model_dump_json()) == bundle.result
    assert load_ai_tag_execution_outcome_v2(bundle.outcome.model_dump_json()) == bundle.outcome
    assert load_ai_tag_trusted_execution_subject(bundle.subject.model_dump_json()) == bundle.subject
    assert (
        load_ai_tag_trusted_runner_attestation(bundle.attestation.model_dump_json())
        == bundle.attestation
    )
    assert load_hybrid_feature_analysis_result_v2(bundle.hybrid.model_dump_json()) == bundle.hybrid
    with pytest.raises(ValueError, match="invalid AI Tag Analysis Result"):
        load_ai_tag_analysis_result(bundle.result.model_dump_json())

    duplicate = bundle.result.model_dump_json().replace(
        '"schema_version":"ai-tag-analysis-result-v2"',
        '"schema_version":"ai-tag-analysis-result-v2","schema_version":"ai-tag-analysis-result-v2"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_ai_tag_analysis_result_v2(duplicate)


def test_injected_transport_and_missing_or_changed_raw_bytes_never_formalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card, envelope, plan, policy = _plan()
    raw_body = _provider_body(envelope)
    credential = _Credential()
    claims = _claims(plan, credential_scope_id=credential.credential_scope_id)
    trusted_inputs = _trusted_inputs(card=card, envelope=envelope, policy=policy)
    events: list[str] = []
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential,
        trusted_plan_inputs=trusted_inputs,
        egress_verifier=_EgressVerifier(events),
        budget_ledger=_BudgetLedger(events),
    )
    capability = gate.authorize(plan=plan, claims=claims)
    artifacts = DeepSeekShadowRunner(
        gate=gate,
        transport=_ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 7)),
    ).run(
        plan=plan,
        claims=claims,
        capability=capability,
        envelope=envelope,
    )
    signer, registry = _trust_objects(claims.trust_domain_id)
    assert artifacts.attempt_receipt.transport_evidence == "injected_untrusted_transport"
    assert not hasattr(hybrid_analysis, "produce_ai_tag_formal_execution_v2")
    with pytest.raises(TypeError, match="unexpected keyword argument 'transport'"):
        DeepSeekFormalExecutionRunnerV2(  # type: ignore[call-arg]
            gate=gate,
            signer=signer,
            registry=registry,
            transport=_ScriptedTransport(DeepSeekHttpResponse(200, raw_body, None, 7)),
        )

    evidence, _, formal_registry = _fixed_run(monkeypatch)
    verifier = AITagFormalExecutionVerifierV2(registry=formal_registry)
    without_raw = _tampered_evidence(evidence, raw_response_body=None)
    with pytest.raises(ValueError, match="requires the original response bytes"):
        verifier.verify(without_raw)
    changed_raw = _tampered_evidence(evidence, raw_response_body=raw_body + b" ")
    with pytest.raises(ValueError, match="differs from original response bytes"):
        verifier.verify(changed_raw)


def test_signature_registry_and_deterministic_projection_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _, registry = _fixed_run(monkeypatch)
    bundle = evidence.bundle

    attestation_payload = bundle.attestation.model_dump(
        mode="json",
        exclude={"attestation_id"},
    )
    signature = str(attestation_payload["signature_hex"])
    attestation_payload["signature_hex"] = ("0" if signature[0] != "0" else "1") + signature[1:]
    attestation_payload["attestation_id"] = canonical_hash(
        "ai-tag-trusted-runner-attestation",
        attestation_payload,
    )
    forged_attestation = type(bundle.attestation).model_validate(attestation_payload)
    forged_bundle = replace(bundle, attestation=forged_attestation)
    forged_evidence = _tampered_evidence(evidence, bundle=forged_bundle)
    with pytest.raises(ValueError, match="signature is invalid"):
        AITagFormalExecutionVerifierV2(registry=registry).verify(forged_evidence)

    assert bundle.result is not None
    result_payload = bundle.result.model_dump(mode="json", exclude={"result_id"})
    result_payload["latency_ms"] = int(result_payload["latency_ms"]) + 1
    result_payload["result_id"] = canonical_hash("ai-tag-result-v2", result_payload)
    forged_result = AITagAnalysisResultV2.model_validate(result_payload)
    forged_result_bundle = replace(bundle, result=forged_result)
    with pytest.raises(ValueError, match="deterministic evidence projection"):
        AITagFormalExecutionVerifierV2(registry=registry).verify(
            _tampered_evidence(evidence, bundle=forged_result_bundle)
        )

    # Standalone self-hashes prove content identity, not cross-artifact truth.
    outcome_payload = bundle.outcome.model_dump(mode="json", exclude={"outcome_id"})
    outcome_payload["result_id"] = f"ai-tag-result-v2:sha256:{'6' * 64}"
    outcome_payload["outcome_id"] = canonical_hash(
        "ai-tag-outcome-v2",
        outcome_payload,
    )
    self_hashed_outcome = type(bundle.outcome).model_validate(outcome_payload)
    with pytest.raises(ValueError, match="Outcome V2 differs"):
        AITagFormalExecutionVerifierV2(registry=registry).verify(
            _tampered_evidence(
                evidence,
                bundle=replace(bundle, outcome=self_hashed_outcome),
            )
        )

    subject_payload = bundle.subject.model_dump(mode="json", exclude={"subject_id"})
    subject_payload["outcome_id"] = f"ai-tag-outcome-v2:sha256:{'5' * 64}"
    subject_payload["subject_id"] = canonical_hash(
        "ai-tag-trusted-execution-subject",
        subject_payload,
    )
    self_hashed_subject = type(bundle.subject).model_validate(subject_payload)
    with pytest.raises(ValueError, match="trusted execution subject differs"):
        AITagFormalExecutionVerifierV2(registry=registry).verify(
            _tampered_evidence(
                evidence,
                bundle=replace(bundle, subject=self_hashed_subject),
            )
        )

    wrong_seed = hashlib.sha256(b"wrong-test-key").digest()
    wrong_private = Ed25519PrivateKey.from_private_bytes(wrong_seed)
    wrong_public = wrong_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    wrong_record = build_ai_tag_trusted_runner_key_record(
        public_key_bytes=wrong_public,
        runner_id=_RUNNER_ID,
        trust_domain_id=registry.trust_domain_id,
        allowed_runner_release_fingerprints=(_RUNNER_RELEASE,),
    )
    wrong_registry_id = compute_ai_tag_runner_registry_id(
        trust_domain_id=registry.trust_domain_id,
        registry_policy_fingerprint=_REGISTRY_POLICY,
        records=(wrong_record,),
    )
    wrong_registry = AITagTrustedRunnerRegistry(
        expected_registry_id=wrong_registry_id,
        trust_domain_id=registry.trust_domain_id,
        registry_policy_fingerprint=_REGISTRY_POLICY,
        records=(wrong_record,),
    )
    with pytest.raises(ValueError, match="pinned registry or policy"):
        AITagFormalExecutionVerifierV2(registry=wrong_registry).verify(evidence)


def test_runtime_trust_objects_are_opaque_and_repeated_runs_keep_run_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, signer, registry = _fixed_run(monkeypatch)
    verifier = AITagFormalExecutionVerifierV2(registry=registry)
    eligibility = verifier.verify(evidence)

    for value in (evidence, signer, registry, verifier, eligibility):
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(AttributeError, match="immutable"):
            delattr(value, "_runtime_sealed")
    assert _PRIVATE_SEED.hex() not in repr(signer)
    assert signer._contexts == {}  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        signer._runner_id = _RUNNER_ID  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        registry._records = {}  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        verifier._registry = registry  # noqa: SLF001
    with pytest.raises(AttributeError, match="immutable"):
        eligibility._positive_tags = ("has_timer",)  # noqa: SLF001

    # A second integrated execution has a new signing event but the same deterministic
    # Plan + Attempt identity under the fixed synthetic response.
    second, second_signer, _ = _fixed_run(monkeypatch)
    assert second_signer._contexts == {}  # noqa: SLF001
    assert second.bundle.outcome.analysis_run_id == evidence.bundle.outcome.analysis_run_id
    assert second.bundle.outcome == evidence.bundle.outcome
    assert second.bundle.result == evidence.bundle.result
    assert (
        second.bundle.subject.formalization_event_id
        != evidence.bundle.subject.formalization_event_id
    )


def test_plain_validation_v1_graph_or_safe_json_is_not_formal_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _, registry = _fixed_run(monkeypatch)
    verifier = AITagFormalExecutionVerifierV2(registry=registry)
    validation = evidence.run_artifacts.response_validation
    assert validation is not None
    with pytest.raises(TypeError, match="complete Formal Execution Evidence V2"):
        verifier.verify(validation)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="complete Formal Execution Evidence V2"):
        verifier.verify(json.loads('{"safe_summary":true}'))  # type: ignore[arg-type]

    # A standalone self-hashed Hybrid V2 is still only content identity. Eligibility
    # requires the complete signed bundle and raw evidence verifier.
    assert isinstance(evidence.bundle.hybrid, HybridFeatureAnalysisResultV2)
    with pytest.raises(TypeError):
        VerifiedAITagFormalExecutionEligibility(  # type: ignore[call-arg]
            construction_token=object(),
            bundle=evidence.bundle,
        )
