from __future__ import annotations

import hashlib
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Literal, NoReturn, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.builders import (
    DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
    verify_model_view_against_card_and_policy,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import (
    VerifiedAITagDispatchEnvelope,
    verify_ai_tag_dispatch_envelope,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    ACTIVE_TAG_COUNT_V1,
    AITagAnalysisRequest,
    AITagJudgment,
    AITagModelView,
    AITagUsage,
    HybridTagState,
    ReviewUnitAnalysisCard,
    StaticDecision,
    reduce_unit_comparison,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
    verify_full_taxonomy_request,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
    AITagShadowDispatchCapability,
    AITagShadowRunArtifacts,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
    verify_deepseek_shadow_run_artifacts,
)

AI_TAG_ANALYSIS_RESULT_V2_SCHEMA_VERSION = "ai-tag-analysis-result-v2"
AI_TAG_EXECUTION_OUTCOME_V2_SCHEMA_VERSION = "ai-tag-execution-outcome-v2"
AI_TAG_TRUSTED_EXECUTION_SUBJECT_SCHEMA_VERSION = "ai-tag-trusted-execution-subject-v1"
AI_TAG_TRUSTED_RUNNER_ATTESTATION_SCHEMA_VERSION = "ai-tag-trusted-runner-attestation-v1"
HYBRID_FEATURE_ANALYSIS_RESULT_V2_SCHEMA_VERSION = "hybrid-feature-analysis-result-v2"

AI_TAG_FORMALIZATION_POLICY_FINGERPRINT = canonical_hash(
    "ai-tag-formalization-policy",
    {
        "policy_version": "ai-tag-formalization-policy-v1",
        "allowed_transport": "httpx_tls_fixed_endpoint",
        "raw_response_rebuild": "mandatory_for_response_received",
        "provider_signature": "not_available",
        "formal_use_scope": "hybrid_retrieval_shadow_only",
        "production_qualified": False,
    },
)

_SIGNATURE_DOMAIN = b"arkts-code-reviewer\0ai-tag-trusted-runner-attestation-v1\0"
_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_CLAIMS_ID = rf"^ai-tag-shadow-claims:sha256:{_HASH}$"
_ATTEMPT_RECEIPT_ID = rf"^ai-tag-attempt-receipt:sha256:{_HASH}$"
_RESPONSE_RECEIPT_ID = rf"^ai-tag-observed-response:sha256:{_HASH}$"
_VALIDATION_ID = rf"^ai-tag-response-validation:sha256:{_HASH}$"
_OBSERVATION_ID = rf"^ai-tag-shadow-observation:sha256:{_HASH}$"
_OUTER_DIAGNOSTIC_ID = rf"^deepseek-outer-response-diagnostic:sha256:{_HASH}$"
_TRUST_DOMAIN_ID = rf"^ai-shadow-trust-domain:sha256:{_HASH}$"
_EGRESS_APPROVAL_ID = rf"^ai-egress-approval:sha256:{_HASH}$"
_BUDGET_RESERVATION_ID = rf"^ai-budget-reservation:sha256:{_HASH}$"
_CREDENTIAL_SCOPE_ID = rf"^deepseek-credential-scope:sha256:{_HASH}$"
_RESULT_V2_ID = rf"^ai-tag-result-v2:sha256:{_HASH}$"
_OUTCOME_V2_ID = rf"^ai-tag-outcome-v2:sha256:{_HASH}$"
_RUN_V2_ID = rf"^ai-tag-run-v2:sha256:{_HASH}$"
_SUBJECT_ID = rf"^ai-tag-trusted-execution-subject:sha256:{_HASH}$"
_ATTESTATION_ID = rf"^ai-tag-trusted-runner-attestation:sha256:{_HASH}$"
_HYBRID_V2_ID = rf"^hybrid-analysis-v2:sha256:{_HASH}$"
_RUNNER_ID = rf"^ai-tag-runner:sha256:{_HASH}$"
_RUNNER_RELEASE_ID = rf"^ai-tag-runner-release:sha256:{_HASH}$"
_RUNNER_KEY_ID = rf"^ai-tag-runner-key:sha256:{_HASH}$"
_RUNNER_REGISTRY_ID = rf"^ai-tag-runner-registry:sha256:{_HASH}$"
_REGISTRY_POLICY_ID = rf"^ai-tag-runner-registry-policy:sha256:{_HASH}$"
_FORMALIZATION_POLICY_ID = rf"^ai-tag-formalization-policy:sha256:{_HASH}$"
_FORMALIZATION_EVENT_ID = r"^ai-tag-formalization-event:uuid:[0-9a-f]{32}$"
_SIGNATURE = r"^[0-9a-f]{128}$"

FormalExecutionStatus = Literal["valid_result", "invalid_output", "unavailable"]


class _ImmutableRuntimeObject:
    """Reject ordinary attribute replacement after trusted construction."""

    __slots__ = ("_runtime_sealed",)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__delattr__(self, name)

    def _seal_runtime_object(self) -> None:
        object.__setattr__(self, "_runtime_sealed", True)


_INNER_INVALID_REASONS = {
    "evidence_out_of_range",
    "incomplete_taxonomy",
    "invalid_json",
    "non_stop_finish_reason",
    "response_empty",
    "schema_invalid",
}
_OUTER_INVALID_REASON = "provider_outer_contract_invalid"
_INVALID_REASONS = {*_INNER_INVALID_REASONS, _OUTER_INVALID_REASON}
_UNAVAILABLE_REASONS = {
    "provider_client_error",
    "provider_rate_limited",
    "provider_response_too_large",
    "provider_server_error",
    "provider_timeout",
    "provider_transport_error",
}
_RESPONSE_UNAVAILABLE_REASONS = {
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
}
_NO_RESPONSE_UNAVAILABLE_REASONS = {
    "provider_response_too_large",
    "provider_timeout",
    "provider_transport_error",
}


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _single_line(value: str, context: str, max_length: int = 500) -> str:
    if (
        not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"{context} must be non-empty, trimmed, single-line, and at most "
            f"{max_length} characters"
        )
    return value


def _canonical_strings(value: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{context} must contain strings")
    normalized = tuple(_single_line(item, context) for item in value)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError(f"{context} must be sorted and unique")
    return normalized


def _utc_timestamp(value: str, context: str) -> str:
    value = _single_line(value, context, 40)
    if not value.endswith("Z"):
        raise ValueError(f"{context} must use canonical UTC Z notation")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{context} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo != UTC or parsed.microsecond != 0:
        raise ValueError(f"{context} must use whole-second UTC precision")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if value != canonical:
        raise ValueError(f"{context} must use canonical UTC formatting")
    return value


def new_ai_tag_formalization_event_id() -> str:
    """Create a signing occurrence nonce, not a provider-execution identity."""

    return f"ai-tag-formalization-event:uuid:{uuid.uuid4().hex}"


class _AITagAnalysisResultV2Payload(FrozenModel):
    schema_version: Literal["ai-tag-analysis-result-v2"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[str, Field(pattern=_RESPONSE_RECEIPT_ID)]
    response_validation_id: Annotated[str, Field(pattern=_VALIDATION_ID)]
    observation_id: Annotated[str, Field(pattern=_OBSERVATION_ID)]
    response_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int, Field(ge=1, le=8_000_000)]
    provider_response_id: Annotated[str, Field(min_length=1, max_length=500)]
    provider: Literal["deepseek"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    system_fingerprint: Annotated[str | None, Field(max_length=500)]
    thinking: Literal["disabled"]
    reasoning_effort: None
    response_format: Literal["json_object"]
    finish_reason: Literal["stop"]
    judgments: tuple[AITagJudgment, ...]
    usage: AITagUsage
    latency_ms: Annotated[int, Field(ge=0)]
    attempt_count: Literal[1]
    output_status: Literal["valid"]
    transport_evidence: Literal["httpx_tls_fixed_endpoint"]
    provider_evidence_scope: Literal["observed_over_tls_not_provider_signed"]
    source_git_provenance_scope: Literal["not_attested"]
    formal_use_scope: Literal["hybrid_retrieval_shadow_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("judgments", mode="before")
    @classmethod
    def parse_judgments(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagAnalysisResultV2.judgments")

    @field_validator("provider_response_id")
    @classmethod
    def validate_provider_response_id(cls, value: str) -> str:
        return _single_line(value, "AITagAnalysisResultV2.provider_response_id")

    @field_validator("system_fingerprint")
    @classmethod
    def validate_system_fingerprint(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _single_line(value, "AITagAnalysisResultV2.system_fingerprint")
        )

    @model_validator(mode="after")
    def validate_taxonomy(self) -> Self:
        tag_ids = tuple(item.tag_id for item in self.judgments)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1 or tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError("AITagAnalysisResultV2 requires canonical unique 24-Tag judgments")
        return self


class AITagAnalysisResultV2(_AITagAnalysisResultV2Payload):
    """Deterministic projection; standalone parsing does not verify runner provenance."""

    result_id: Annotated[str, Field(pattern=_RESULT_V2_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-result-v2",
            identity_payload(self, "result_id"),
        )
        if self.result_id != expected:
            raise ValueError("AITagAnalysisResultV2.result_id does not match contents")
        return self


class _AITagExecutionOutcomeV2Payload(FrozenModel):
    schema_version: Literal["ai-tag-execution-outcome-v2"]
    analysis_run_id: Annotated[str, Field(pattern=_RUN_V2_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[
        str | None,
        Field(pattern=_RESPONSE_RECEIPT_ID),
    ]
    response_validation_id: Annotated[str | None, Field(pattern=_VALIDATION_ID)]
    outer_diagnostic_id: Annotated[str | None, Field(pattern=_OUTER_DIAGNOSTIC_ID)]
    observation_id: Annotated[str, Field(pattern=_OBSERVATION_ID)]
    response_body_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int | None, Field(ge=0, le=8_000_000)]
    status: FormalExecutionStatus
    result_id: Annotated[str | None, Field(pattern=_RESULT_V2_ID)]
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]
    attempt_count: Literal[1]
    trust_domain_id: Annotated[str, Field(pattern=_TRUST_DOMAIN_ID)]
    egress_approval_id: Annotated[str, Field(pattern=_EGRESS_APPROVAL_ID)]
    budget_reservation_id: Annotated[str, Field(pattern=_BUDGET_RESERVATION_ID)]
    credential_scope_id: Annotated[str, Field(pattern=_CREDENTIAL_SCOPE_ID)]
    transport_evidence: Literal["httpx_tls_fixed_endpoint"]
    provider_evidence_scope: Literal[
        "http_response_observed_over_tls_not_provider_signed",
        "fixed_tls_transport_attempt_no_complete_verified_response",
    ]
    formal_use_scope: Literal["hybrid_retrieval_shadow_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        value = _single_line(value, "AITagExecutionOutcomeV2.reason_code", 100)
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("AITagExecutionOutcomeV2.reason_code must use snake case")
        return value

    @model_validator(mode="after")
    def validate_status_matrix(self) -> Self:
        response_pair = (self.response_body_sha256, self.response_body_size_bytes)
        if (response_pair[0] is None) != (response_pair[1] is None):
            raise ValueError("Outcome V2 response hash and size must be present together")
        expected_provider_scope = (
            "http_response_observed_over_tls_not_provider_signed"
            if response_pair[0] is not None
            else "fixed_tls_transport_attempt_no_complete_verified_response"
        )
        if self.provider_evidence_scope != expected_provider_scope:
            raise ValueError("Outcome V2 provider evidence scope differs from response presence")
        if self.status == "valid_result":
            if (
                self.result_id is None
                or self.provider_response_receipt_id is None
                or self.response_validation_id is None
                or self.outer_diagnostic_id is not None
                or self.reason_code != "provider_response_valid"
                or response_pair[0] is None
            ):
                raise ValueError("valid Result V2 outcome has incomplete provenance")
        elif self.status == "invalid_output":
            if self.result_id is not None or self.reason_code not in _INVALID_REASONS:
                raise ValueError("invalid-output Result V2 outcome has an invalid matrix")
            if response_pair[0] is None:
                raise ValueError("invalid output must bind the received response body")
            inner = (
                self.provider_response_receipt_id is not None
                and self.response_validation_id is not None
                and self.outer_diagnostic_id is None
                and self.reason_code in _INNER_INVALID_REASONS
            )
            outer = (
                self.provider_response_receipt_id is None
                and self.response_validation_id is None
                and self.outer_diagnostic_id is not None
                and self.reason_code == _OUTER_INVALID_REASON
            )
            if not (inner or outer):
                raise ValueError("invalid output must bind either inner or outer diagnostics")
        elif (
            self.result_id is not None
            or self.reason_code not in _UNAVAILABLE_REASONS
            or self.provider_response_receipt_id is not None
            or self.response_validation_id is not None
            or self.outer_diagnostic_id is not None
        ):
            raise ValueError("unavailable Result V2 outcome has an invalid matrix")
        elif (self.reason_code in _RESPONSE_UNAVAILABLE_REASONS) != (response_pair[0] is not None):
            raise ValueError("unavailable Outcome V2 response presence differs from reason")
        return self


class AITagExecutionOutcomeV2(_AITagExecutionOutcomeV2Payload):
    """Attempted-Plan projection; only a verified complete Bundle is authoritative."""

    outcome_id: Annotated[str, Field(pattern=_OUTCOME_V2_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-outcome-v2",
            identity_payload(self, "outcome_id"),
        )
        if self.outcome_id != expected:
            raise ValueError("AITagExecutionOutcomeV2.outcome_id does not match contents")
        return self


class _AITagTrustedExecutionSubjectPayload(FrozenModel):
    schema_version: Literal["ai-tag-trusted-execution-subject-v1"]
    formalization_event_id: Annotated[str, Field(pattern=_FORMALIZATION_EVENT_ID)]
    formalization_started_at: str
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[
        str | None,
        Field(pattern=_RESPONSE_RECEIPT_ID),
    ]
    response_validation_id: Annotated[str | None, Field(pattern=_VALIDATION_ID)]
    outer_diagnostic_id: Annotated[str | None, Field(pattern=_OUTER_DIAGNOSTIC_ID)]
    observation_id: Annotated[str, Field(pattern=_OBSERVATION_ID)]
    response_body_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int | None, Field(ge=0, le=8_000_000)]
    result_id: Annotated[str | None, Field(pattern=_RESULT_V2_ID)]
    outcome_id: Annotated[str, Field(pattern=_OUTCOME_V2_ID)]
    formal_status: FormalExecutionStatus
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]
    trust_domain_id: Annotated[str, Field(pattern=_TRUST_DOMAIN_ID)]
    egress_approval_id: Annotated[str, Field(pattern=_EGRESS_APPROVAL_ID)]
    budget_reservation_id: Annotated[str, Field(pattern=_BUDGET_RESERVATION_ID)]
    credential_scope_id: Annotated[str, Field(pattern=_CREDENTIAL_SCOPE_ID)]
    runner_id: Annotated[str, Field(pattern=_RUNNER_ID)]
    runner_release_fingerprint: Annotated[str, Field(pattern=_RUNNER_RELEASE_ID)]
    runner_key_id: Annotated[str, Field(pattern=_RUNNER_KEY_ID)]
    runner_registry_id: Annotated[str, Field(pattern=_RUNNER_REGISTRY_ID)]
    registry_policy_fingerprint: Annotated[str, Field(pattern=_REGISTRY_POLICY_ID)]
    formalization_policy_fingerprint: Annotated[
        str,
        Field(pattern=_FORMALIZATION_POLICY_ID),
    ]
    provider: Literal["deepseek"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    transport_evidence: Literal["httpx_tls_fixed_endpoint"]
    network_observation: Literal["observed_by_fixed_httpx_transport"]
    raw_response_rebuild_scope: Literal[
        "passed_complete_http_response",
        "not_applicable_no_complete_http_response",
    ]
    provider_evidence_scope: Literal[
        "http_response_observed_over_tls_not_provider_signed",
        "fixed_tls_transport_attempt_no_complete_verified_response",
    ]
    upstream_rebuild_scope: Literal["caller_roots_rebuilt_not_parser_reviewunit_or_git_attestation"]
    egress_authority_scope: Literal[
        "process_local_verifier_reference_not_external_authority_attestation"
    ]
    budget_authority_scope: Literal[
        "process_local_ledger_reference_not_production_budget_attestation"
    ]
    runner_release_scope: Literal["registry_allowlisted_claim_not_code_git_or_remote_attestation"]
    source_git_provenance_scope: Literal["not_attested"]
    formalization_event_scope: Literal[
        "runner_signed_nonce_not_provider_execution_or_time_authority"
    ]
    formal_use_scope: Literal["hybrid_retrieval_shadow_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("formalization_started_at")
    @classmethod
    def validate_formalization_started_at(cls, value: str) -> str:
        return _utc_timestamp(
            value,
            "AITagTrustedExecutionSubject.formalization_started_at",
        )

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        value = _single_line(value, "AITagTrustedExecutionSubject.reason_code", 100)
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("trusted execution reason_code must use snake case")
        return value

    @model_validator(mode="after")
    def validate_status_matrix(self) -> Self:
        if self.formal_status == "valid_result" and self.result_id is None:
            raise ValueError("valid trusted execution subject requires Result V2")
        if self.formal_status != "valid_result" and self.result_id is not None:
            raise ValueError("non-valid trusted execution subject cannot carry Result V2")
        response_pair = (self.response_body_sha256, self.response_body_size_bytes)
        if (response_pair[0] is None) != (response_pair[1] is None):
            raise ValueError("trusted subject response hash and size must be present together")
        expected_scopes = (
            (
                "passed_complete_http_response",
                "http_response_observed_over_tls_not_provider_signed",
            )
            if response_pair[0] is not None
            else (
                "not_applicable_no_complete_http_response",
                "fixed_tls_transport_attempt_no_complete_verified_response",
            )
        )
        if (
            self.raw_response_rebuild_scope,
            self.provider_evidence_scope,
        ) != expected_scopes:
            raise ValueError("trusted subject response scopes differ from response presence")
        if self.formal_status == "valid_result":
            valid = (
                self.reason_code == "provider_response_valid"
                and self.provider_response_receipt_id is not None
                and self.response_validation_id is not None
                and self.outer_diagnostic_id is None
                and response_pair[0] is not None
            )
            if not valid:
                raise ValueError("valid trusted subject has an invalid artifact matrix")
        elif self.formal_status == "invalid_output":
            inner = (
                self.reason_code in _INNER_INVALID_REASONS
                and self.provider_response_receipt_id is not None
                and self.response_validation_id is not None
                and self.outer_diagnostic_id is None
            )
            outer = (
                self.reason_code == _OUTER_INVALID_REASON
                and self.provider_response_receipt_id is None
                and self.response_validation_id is None
                and self.outer_diagnostic_id is not None
            )
            if response_pair[0] is None or not (inner or outer):
                raise ValueError("invalid-output trusted subject has an invalid matrix")
        elif (
            self.reason_code not in _UNAVAILABLE_REASONS
            or self.provider_response_receipt_id is not None
            or self.response_validation_id is not None
            or self.outer_diagnostic_id is not None
            or (self.reason_code in _RESPONSE_UNAVAILABLE_REASONS) != (response_pair[0] is not None)
        ):
            raise ValueError("unavailable trusted subject has an invalid artifact matrix")
        return self


class AITagTrustedExecutionSubject(_AITagTrustedExecutionSubjectPayload):
    subject_id: Annotated[str, Field(pattern=_SUBJECT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-trusted-execution-subject",
            identity_payload(self, "subject_id"),
        )
        if self.subject_id != expected:
            raise ValueError("trusted execution subject ID does not match contents")
        return self


class _AITagTrustedRunnerAttestationPayload(FrozenModel):
    schema_version: Literal["ai-tag-trusted-runner-attestation-v1"]
    subject_id: Annotated[str, Field(pattern=_SUBJECT_ID)]
    trust_domain_id: Annotated[str, Field(pattern=_TRUST_DOMAIN_ID)]
    runner_id: Annotated[str, Field(pattern=_RUNNER_ID)]
    runner_release_fingerprint: Annotated[str, Field(pattern=_RUNNER_RELEASE_ID)]
    runner_key_id: Annotated[str, Field(pattern=_RUNNER_KEY_ID)]
    runner_registry_id: Annotated[str, Field(pattern=_RUNNER_REGISTRY_ID)]
    registry_policy_fingerprint: Annotated[str, Field(pattern=_REGISTRY_POLICY_ID)]
    formalization_policy_fingerprint: Annotated[
        str,
        Field(pattern=_FORMALIZATION_POLICY_ID),
    ]
    signature_algorithm: Literal["ed25519"]
    signature_encoding: Literal["lowercase_hex"]
    signature_hex: Annotated[str, Field(pattern=_SIGNATURE)]
    signed_payload_sha256: Annotated[str, Field(pattern=_SHA256)]
    attested_at: str
    formal_use_scope: Literal["hybrid_retrieval_shadow_only"]
    provider_signature_scope: Literal["not_available"]
    source_git_provenance_scope: Literal["not_attested"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("attested_at")
    @classmethod
    def validate_attested_at(cls, value: str) -> str:
        return _utc_timestamp(value, "AITagTrustedRunnerAttestation.attested_at")


class AITagTrustedRunnerAttestation(_AITagTrustedRunnerAttestationPayload):
    attestation_id: Annotated[str, Field(pattern=_ATTESTATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-trusted-runner-attestation",
            identity_payload(self, "attestation_id"),
        )
        if self.attestation_id != expected:
            raise ValueError("trusted runner attestation ID does not match contents")
        return self


class _HybridFeatureAnalysisResultV2Payload(FrozenModel):
    schema_version: Literal["hybrid-feature-analysis-result-v2"]
    unit_id: Annotated[str, Field(min_length=1, max_length=1_000)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    trusted_execution_subject_id: Annotated[str, Field(pattern=_SUBJECT_ID)]
    trusted_runner_attestation_id: Annotated[str, Field(pattern=_ATTESTATION_ID)]
    ai_execution_outcome_id: Annotated[str, Field(pattern=_OUTCOME_V2_ID)]
    ai_result_id: Annotated[str | None, Field(pattern=_RESULT_V2_ID)]
    tag_states: tuple[HybridTagState, ...]
    diagnostics: tuple[str, ...]
    ai_signal_scope: Literal["attestation_reference_requires_full_bundle_verification"]
    formal_review_question_binding: Literal["unchanged_static_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("tag_states", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "HybridFeatureAnalysisResultV2 sequence")

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        return _single_line(value, "HybridFeatureAnalysisResultV2.unit_id", 1_000)

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, "HybridFeatureAnalysisResultV2.diagnostics")

    @model_validator(mode="after")
    def validate_tag_states(self) -> Self:
        tag_ids = tuple(state.tag_id for state in self.tag_states)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1 or tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError("HybridFeatureAnalysisResultV2 requires canonical 24 Tag states")
        decisions = tuple(state.ai_unit_decision is not None for state in self.tag_states)
        if self.ai_result_id is None and any(decisions):
            raise ValueError("Hybrid V2 without Result V2 cannot carry AI decisions")
        if self.ai_result_id is not None and not all(decisions):
            raise ValueError("Hybrid V2 with Result V2 requires all AI decisions")
        return self


class HybridFeatureAnalysisResultV2(_HybridFeatureAnalysisResultV2Payload):
    analysis_id: Annotated[str, Field(pattern=_HYBRID_V2_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "hybrid-analysis-v2",
            identity_payload(self, "analysis_id"),
        )
        if self.analysis_id != expected:
            raise ValueError("HybridFeatureAnalysisResultV2.analysis_id does not match contents")
        return self


def runner_key_id_from_public_key(public_key_bytes: bytes) -> str:
    if not isinstance(public_key_bytes, bytes):
        raise TypeError("runner public key must use bytes")
    if len(public_key_bytes) != 32:
        raise ValueError("Ed25519 runner public key must contain exactly 32 bytes")
    return "ai-tag-runner-key:sha256:" + hashlib.sha256(public_key_bytes).hexdigest()


def build_ai_tag_trusted_runner_key_record(
    *,
    public_key_bytes: bytes,
    runner_id: str,
    trust_domain_id: str,
    allowed_runner_release_fingerprints: tuple[str, ...],
    status: Literal["active", "revoked"] = "active",
) -> AITagTrustedRunnerKeyRecord:
    """Build a public registry record without constructing or retaining a signer."""

    public_key_bytes = bytes(public_key_bytes)
    return AITagTrustedRunnerKeyRecord(
        runner_id=runner_id,
        trust_domain_id=trust_domain_id,
        runner_key_id=runner_key_id_from_public_key(public_key_bytes),
        public_key_bytes=public_key_bytes,
        allowed_runner_release_fingerprints=allowed_runner_release_fingerprints,
        status=status,
    )


@dataclass(frozen=True, repr=False)
class AITagTrustedRunnerKeyRecord:
    """One deployment-pinned public runner key; never sourced from evidence."""

    runner_id: str
    trust_domain_id: str
    runner_key_id: str
    public_key_bytes: bytes
    allowed_runner_release_fingerprints: tuple[str, ...]
    status: Literal["active", "revoked"] = "active"

    def __post_init__(self) -> None:
        if re_fullmatch(_RUNNER_ID, self.runner_id) is None:
            raise ValueError("invalid trusted runner identity")
        if re_fullmatch(_TRUST_DOMAIN_ID, self.trust_domain_id) is None:
            raise ValueError("invalid trusted runner trust-domain identity")
        public_key = bytes(self.public_key_bytes)
        expected_key_id = runner_key_id_from_public_key(public_key)
        if self.runner_key_id != expected_key_id:
            raise ValueError("runner key ID does not match Ed25519 public key bytes")
        releases = tuple(self.allowed_runner_release_fingerprints)
        if not releases or releases != tuple(sorted(set(releases))):
            raise ValueError("allowed runner releases must be sorted, unique, and non-empty")
        if any(re_fullmatch(_RUNNER_RELEASE_ID, item) is None for item in releases):
            raise ValueError("invalid trusted runner release fingerprint")
        Ed25519PublicKey.from_public_bytes(public_key)
        object.__setattr__(self, "public_key_bytes", public_key)
        object.__setattr__(self, "allowed_runner_release_fingerprints", releases)

    def __repr__(self) -> str:
        return (
            "AITagTrustedRunnerKeyRecord("
            f"runner_id={self.runner_id!r}, runner_key_id={self.runner_key_id!r}, "
            f"status={self.status!r}, public_key=<redacted-runtime-root>)"
        )

    def registry_payload(self) -> dict[str, object]:
        return {
            "runner_id": self.runner_id,
            "trust_domain_id": self.trust_domain_id,
            "runner_key_id": self.runner_key_id,
            "public_key_hex": self.public_key_bytes.hex(),
            "allowed_runner_release_fingerprints": list(self.allowed_runner_release_fingerprints),
            "status": self.status,
        }


def re_fullmatch(pattern: str, value: object) -> object | None:
    """Tiny local helper that keeps regex validation out of runtime evidence objects."""

    if not isinstance(value, str):
        return None
    return re.fullmatch(pattern, value)


def compute_ai_tag_runner_registry_id(
    *,
    trust_domain_id: str,
    registry_policy_fingerprint: str,
    records: tuple[AITagTrustedRunnerKeyRecord, ...],
) -> str:
    if re_fullmatch(_TRUST_DOMAIN_ID, trust_domain_id) is None:
        raise ValueError("invalid registry trust-domain identity")
    if re_fullmatch(_REGISTRY_POLICY_ID, registry_policy_fingerprint) is None:
        raise ValueError("invalid registry policy fingerprint")
    ordered = tuple(sorted(records, key=lambda item: item.runner_key_id))
    if not ordered or len({record.runner_key_id for record in ordered}) != len(ordered):
        raise ValueError("runner registry records must have unique key IDs")
    if any(record.trust_domain_id != trust_domain_id for record in ordered):
        raise ValueError("runner registry records cross trust domains")
    return canonical_hash(
        "ai-tag-runner-registry",
        {
            "schema_version": "ai-tag-runner-registry-v1",
            "trust_domain_id": trust_domain_id,
            "registry_policy_fingerprint": registry_policy_fingerprint,
            "records": [record.registry_payload() for record in ordered],
        },
    )


def _attestation_message_payload(
    *,
    subject: AITagTrustedExecutionSubject,
    trust_domain_id: str,
    runner_id: str,
    runner_release_fingerprint: str,
    runner_key_id: str,
    runner_registry_id: str,
    registry_policy_fingerprint: str,
    formalization_policy_fingerprint: str,
    attested_at: str,
) -> dict[str, object]:
    return {
        "schema_version": AI_TAG_TRUSTED_RUNNER_ATTESTATION_SCHEMA_VERSION,
        "subject": subject.model_dump(mode="json"),
        "trust_domain_id": trust_domain_id,
        "runner_id": runner_id,
        "runner_release_fingerprint": runner_release_fingerprint,
        "runner_key_id": runner_key_id,
        "runner_registry_id": runner_registry_id,
        "registry_policy_fingerprint": registry_policy_fingerprint,
        "formalization_policy_fingerprint": formalization_policy_fingerprint,
        "attested_at": attested_at,
        "formal_use_scope": "hybrid_retrieval_shadow_only",
        "provider_signature_scope": "not_available",
        "source_git_provenance_scope": "not_attested",
        "evidence_qualification_status": "not_qualified",
        "production_qualified": False,
    }


def _attestation_message(payload: dict[str, object]) -> bytes:
    return _SIGNATURE_DOMAIN + canonical_json(payload).encode("utf-8")


class AITagTrustedRunnerRegistry(_ImmutableRuntimeObject):
    """Deployment-owned, explicitly pinned Ed25519 verification root."""

    __slots__ = (
        "_records",
        "_registry_id",
        "_registry_policy_fingerprint",
        "_trust_domain_id",
    )

    def __init__(
        self,
        *,
        expected_registry_id: str,
        trust_domain_id: str,
        registry_policy_fingerprint: str,
        records: tuple[AITagTrustedRunnerKeyRecord, ...],
    ) -> None:
        records = tuple(records)
        computed = compute_ai_tag_runner_registry_id(
            trust_domain_id=trust_domain_id,
            registry_policy_fingerprint=registry_policy_fingerprint,
            records=records,
        )
        if expected_registry_id != computed:
            raise ValueError("runner registry differs from externally pinned identity")
        self._registry_id = computed
        self._trust_domain_id = trust_domain_id
        self._registry_policy_fingerprint = registry_policy_fingerprint
        self._records = MappingProxyType({record.runner_key_id: record for record in records})
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return (
            "AITagTrustedRunnerRegistry("
            f"registry_id={self._registry_id!r}, keys=<deployment-pinned>)"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("trusted runner registries are runtime roots and are not serializable")

    @property
    def registry_id(self) -> str:
        return self._registry_id

    @property
    def trust_domain_id(self) -> str:
        return self._trust_domain_id

    @property
    def registry_policy_fingerprint(self) -> str:
        return self._registry_policy_fingerprint

    def _verify_registry_integrity(self) -> None:
        records = tuple(self._records.values())
        computed = compute_ai_tag_runner_registry_id(
            trust_domain_id=self._trust_domain_id,
            registry_policy_fingerprint=self._registry_policy_fingerprint,
            records=records,
        )
        if computed != self._registry_id:
            raise ValueError("runner registry runtime state differs from pinned identity")

    def _verify_signer_configuration(
        self,
        signer: AITagTrustedRunnerSigner,
    ) -> None:
        self._verify_registry_integrity()
        if type(signer) is not AITagTrustedRunnerSigner:
            raise TypeError("runner registry requires the repository signer type")
        if (
            signer._trust_domain_id != self._trust_domain_id  # noqa: SLF001
            or signer._runner_registry_id != self._registry_id  # noqa: SLF001
            or signer._registry_policy_fingerprint  # noqa: SLF001
            != self._registry_policy_fingerprint
        ):
            raise ValueError("trusted runner signer differs from pinned registry")
        record = self._records.get(signer._runner_key_id)  # noqa: SLF001
        if record is None:
            raise ValueError("trusted runner signer uses an unknown key")
        if record.status != "active":
            raise ValueError("trusted runner signer uses a revoked key")
        if (
            record.trust_domain_id != self._trust_domain_id
            or signer._runner_id != record.runner_id  # noqa: SLF001
            or signer._runner_release_fingerprint  # noqa: SLF001
            not in record.allowed_runner_release_fingerprints
        ):
            raise ValueError("trusted runner signer identity or release is not allowed")

    def verify(
        self,
        *,
        subject: AITagTrustedExecutionSubject,
        attestation: AITagTrustedRunnerAttestation,
    ) -> None:
        self._verify_registry_integrity()
        subject = AITagTrustedExecutionSubject.model_validate(subject.model_dump(mode="json"))
        attestation = AITagTrustedRunnerAttestation.model_validate(
            attestation.model_dump(mode="json")
        )
        if (
            subject.subject_id != attestation.subject_id
            or subject.trust_domain_id != self._trust_domain_id
            or attestation.trust_domain_id != self._trust_domain_id
            or subject.runner_registry_id != self._registry_id
            or attestation.runner_registry_id != self._registry_id
            or subject.registry_policy_fingerprint != self._registry_policy_fingerprint
            or attestation.registry_policy_fingerprint != self._registry_policy_fingerprint
            or subject.formalization_policy_fingerprint != AI_TAG_FORMALIZATION_POLICY_FINGERPRINT
            or attestation.formalization_policy_fingerprint
            != AI_TAG_FORMALIZATION_POLICY_FINGERPRINT
        ):
            raise ValueError("runner attestation differs from pinned registry or policy")
        record = self._records.get(attestation.runner_key_id)
        if record is None:
            raise ValueError("runner attestation uses an unknown key")
        if record.status != "active":
            raise ValueError("runner attestation uses a revoked key")
        if (
            record.trust_domain_id != self._trust_domain_id
            or subject.runner_id != record.runner_id
            or attestation.runner_id != record.runner_id
            or subject.runner_key_id != record.runner_key_id
            or subject.runner_release_fingerprint != attestation.runner_release_fingerprint
            or subject.runner_release_fingerprint not in record.allowed_runner_release_fingerprints
        ):
            raise ValueError("runner attestation identity or release is not allowed")
        if attestation.attested_at < subject.formalization_started_at:
            raise ValueError("runner attestation predates its formalization context")
        message_payload = _attestation_message_payload(
            subject=subject,
            trust_domain_id=attestation.trust_domain_id,
            runner_id=attestation.runner_id,
            runner_release_fingerprint=attestation.runner_release_fingerprint,
            runner_key_id=attestation.runner_key_id,
            runner_registry_id=attestation.runner_registry_id,
            registry_policy_fingerprint=attestation.registry_policy_fingerprint,
            formalization_policy_fingerprint=(attestation.formalization_policy_fingerprint),
            attested_at=attestation.attested_at,
        )
        message = _attestation_message(message_payload)
        expected_hash = "sha256:" + hashlib.sha256(message).hexdigest()
        if attestation.signed_payload_sha256 != expected_hash:
            raise ValueError("runner attestation signed-payload hash differs")
        try:
            Ed25519PublicKey.from_public_bytes(record.public_key_bytes).verify(
                bytes.fromhex(attestation.signature_hex),
                message,
            )
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("runner attestation signature is invalid") from exc


_SIGNING_CONTEXT_TOKEN = object()
_INTEGRATED_CAPTURE_TOKEN = object()


class _AITagRunnerFormalizationContext:
    """Opaque single-use signing event; it is not a provider-run occurrence proof."""

    __slots__ = (
        "_construction_token",
        "_consumed",
        "_formalization_event_id",
        "_formalization_started_at",
        "_plan_id",
        "_request_id",
        "_signer_nonce",
        "_wire_body_sha256",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        signer_nonce: str,
        formalization_event_id: str,
        formalization_started_at: str,
        plan_id: str,
        request_id: str,
        wire_body_sha256: str,
    ) -> None:
        if construction_token is not _SIGNING_CONTEXT_TOKEN:
            raise TypeError("signing contexts can only be created by a trusted runner signer")
        self._construction_token = construction_token
        self._consumed = False
        self._signer_nonce = signer_nonce
        self._formalization_event_id = formalization_event_id
        self._formalization_started_at = formalization_started_at
        self._plan_id = plan_id
        self._request_id = request_id
        self._wire_body_sha256 = wire_body_sha256

    def __repr__(self) -> str:
        return "_AITagRunnerFormalizationContext(<opaque-single-use>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("runner signing contexts are not serializable")


class _AITagIntegratedExecutionCapture(_ImmutableRuntimeObject):
    """Opaque in-process hand-off from the verified runtime sink to the signer."""

    __slots__ = ("context", "raw_response_body", "run_artifacts")

    def __init__(
        self,
        *,
        construction_token: object,
        context: _AITagRunnerFormalizationContext,
        run_artifacts: AITagShadowRunArtifacts,
        raw_response_body: bytes | None,
    ) -> None:
        if construction_token is not _INTEGRATED_CAPTURE_TOKEN:
            raise TypeError("integrated execution captures are runner-owned")
        if not isinstance(context, _AITagRunnerFormalizationContext):
            raise TypeError("integrated execution capture requires a signing context")
        if not isinstance(run_artifacts, AITagShadowRunArtifacts):
            raise TypeError("integrated execution capture requires run artifacts")
        self.context = context
        self.run_artifacts = run_artifacts
        self.raw_response_body = None if raw_response_body is None else bytes(raw_response_body)
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return "_AITagIntegratedExecutionCapture(<opaque-raw-body-redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("integrated execution captures are not serializable")


class AITagTrustedRunnerSigner(_ImmutableRuntimeObject):
    """Runner-held Ed25519 key with no arbitrary-payload signing API."""

    __slots__ = (
        "_contexts",
        "_lock",
        "_private_key",
        "_registry_policy_fingerprint",
        "_runner_id",
        "_runner_key_id",
        "_runner_registry_id",
        "_runner_release_fingerprint",
        "_trust_domain_id",
    )

    def __init__(
        self,
        *,
        private_key: Ed25519PrivateKey,
        trust_domain_id: str,
        runner_id: str,
        runner_release_fingerprint: str,
        runner_registry_id: str,
        registry_policy_fingerprint: str,
    ) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("trusted runner signer requires an Ed25519 private key")
        for pattern, value, label in (
            (_TRUST_DOMAIN_ID, trust_domain_id, "trust domain"),
            (_RUNNER_ID, runner_id, "runner"),
            (_RUNNER_RELEASE_ID, runner_release_fingerprint, "runner release"),
            (_RUNNER_REGISTRY_ID, runner_registry_id, "runner registry"),
            (_REGISTRY_POLICY_ID, registry_policy_fingerprint, "registry policy"),
        ):
            if re_fullmatch(pattern, value) is None:
                raise ValueError(f"invalid {label} identity")
        public_key_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._private_key = private_key
        self._trust_domain_id = trust_domain_id
        self._runner_id = runner_id
        self._runner_release_fingerprint = runner_release_fingerprint
        self._runner_key_id = runner_key_id_from_public_key(public_key_bytes)
        self._runner_registry_id = runner_registry_id
        self._registry_policy_fingerprint = registry_policy_fingerprint
        self._contexts: dict[str, Literal["issued", "consumed"]] = {}
        self._lock = threading.Lock()
        self._seal_runtime_object()

    @classmethod
    def from_private_key_bytes(
        cls,
        *,
        private_key_bytes: bytes,
        trust_domain_id: str,
        runner_id: str,
        runner_release_fingerprint: str,
        runner_registry_id: str,
        registry_policy_fingerprint: str,
    ) -> AITagTrustedRunnerSigner:
        if not isinstance(private_key_bytes, bytes) or len(private_key_bytes) != 32:
            raise ValueError("Ed25519 private key seed must contain exactly 32 bytes")
        return cls(
            private_key=Ed25519PrivateKey.from_private_bytes(bytes(private_key_bytes)),
            trust_domain_id=trust_domain_id,
            runner_id=runner_id,
            runner_release_fingerprint=runner_release_fingerprint,
            runner_registry_id=runner_registry_id,
            registry_policy_fingerprint=registry_policy_fingerprint,
        )

    def __repr__(self) -> str:
        return (
            "AITagTrustedRunnerSigner("
            f"runner_id={self._runner_id!r}, runner_key_id={self._runner_key_id!r}, "
            "private_key=<redacted>)"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("trusted runner signers are not serializable")

    @property
    def runner_key_id(self) -> str:
        return self._runner_key_id

    def public_key_record(
        self,
        *,
        allowed_runner_release_fingerprints: tuple[str, ...] | None = None,
        status: Literal["active", "revoked"] = "active",
    ) -> AITagTrustedRunnerKeyRecord:
        public_key_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return AITagTrustedRunnerKeyRecord(
            runner_id=self._runner_id,
            trust_domain_id=self._trust_domain_id,
            runner_key_id=self._runner_key_id,
            public_key_bytes=public_key_bytes,
            allowed_runner_release_fingerprints=(
                (self._runner_release_fingerprint,)
                if allowed_runner_release_fingerprints is None
                else allowed_runner_release_fingerprints
            ),
            status=status,
        )

    def _begin_formalization(
        self,
        plan: AITagShadowDispatchPlan,
    ) -> _AITagRunnerFormalizationContext:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        event_id = new_ai_tag_formalization_event_id()
        nonce = uuid.uuid4().hex
        started_at = datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            if len(self._contexts) >= 1_024:
                raise RuntimeError("trusted runner has too many pending formalization contexts")
            self._contexts[nonce] = "issued"
        return _AITagRunnerFormalizationContext(
            construction_token=_SIGNING_CONTEXT_TOKEN,
            signer_nonce=nonce,
            formalization_event_id=event_id,
            formalization_started_at=started_at,
            plan_id=plan.plan_id,
            request_id=plan.request_id,
            wire_body_sha256=plan.wire_body_sha256,
        )

    def _attest_verified_subject(
        self,
        *,
        context: _AITagRunnerFormalizationContext,
        subject: AITagTrustedExecutionSubject,
    ) -> AITagTrustedRunnerAttestation:
        if not isinstance(context, _AITagRunnerFormalizationContext):
            raise TypeError("trusted attestation requires a runner formalization context")
        if context._consumed:  # noqa: SLF001
            raise ValueError("runner signing context is invalid or already consumed")
        if (
            context._construction_token is not _SIGNING_CONTEXT_TOKEN  # noqa: SLF001
            or context._plan_id != subject.plan_id  # noqa: SLF001
            or context._request_id != subject.request_id  # noqa: SLF001
            or context._wire_body_sha256 != subject.wire_body_sha256  # noqa: SLF001
            or context._formalization_event_id  # noqa: SLF001
            != subject.formalization_event_id
            or context._formalization_started_at  # noqa: SLF001
            != subject.formalization_started_at
            or subject.trust_domain_id != self._trust_domain_id
            or subject.runner_id != self._runner_id
            or subject.runner_release_fingerprint != self._runner_release_fingerprint
            or subject.runner_key_id != self._runner_key_id
            or subject.runner_registry_id != self._runner_registry_id
            or subject.registry_policy_fingerprint != self._registry_policy_fingerprint
        ):
            raise ValueError("trusted execution subject differs from signing context or signer")
        nonce = context._signer_nonce  # noqa: SLF001
        with self._lock:
            if self._contexts.pop(nonce, None) != "issued":
                raise ValueError("runner signing context is invalid or already consumed")
            # Consume before invoking the private key so failures cannot turn this into
            # a reusable signing oracle.
            context._consumed = True  # noqa: SLF001
        attested_at = datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = _attestation_message_payload(
            subject=subject,
            trust_domain_id=self._trust_domain_id,
            runner_id=self._runner_id,
            runner_release_fingerprint=self._runner_release_fingerprint,
            runner_key_id=self._runner_key_id,
            runner_registry_id=self._runner_registry_id,
            registry_policy_fingerprint=self._registry_policy_fingerprint,
            formalization_policy_fingerprint=AI_TAG_FORMALIZATION_POLICY_FINGERPRINT,
            attested_at=attested_at,
        )
        message = _attestation_message(payload)
        signature = self._private_key.sign(message)
        return seal_payload(
            {
                "schema_version": AI_TAG_TRUSTED_RUNNER_ATTESTATION_SCHEMA_VERSION,
                "subject_id": subject.subject_id,
                "trust_domain_id": self._trust_domain_id,
                "runner_id": self._runner_id,
                "runner_release_fingerprint": self._runner_release_fingerprint,
                "runner_key_id": self._runner_key_id,
                "runner_registry_id": self._runner_registry_id,
                "registry_policy_fingerprint": self._registry_policy_fingerprint,
                "formalization_policy_fingerprint": (AI_TAG_FORMALIZATION_POLICY_FINGERPRINT),
                "signature_algorithm": "ed25519",
                "signature_encoding": "lowercase_hex",
                "signature_hex": signature.hex(),
                "signed_payload_sha256": ("sha256:" + hashlib.sha256(message).hexdigest()),
                "attested_at": attested_at,
                "formal_use_scope": "hybrid_retrieval_shadow_only",
                "provider_signature_scope": "not_available",
                "source_git_provenance_scope": "not_attested",
                "evidence_qualification_status": "not_qualified",
                "production_qualified": False,
            },
            payload_type=_AITagTrustedRunnerAttestationPayload,
            sealed_type=AITagTrustedRunnerAttestation,
            identity_field="attestation_id",
            identity_prefix="ai-tag-trusted-runner-attestation",
            context="AI Tag Trusted Runner Attestation",
        )

    def _cancel_formalization(
        self,
        context: _AITagRunnerFormalizationContext,
    ) -> None:
        """Consume an issued context after a failed integrated run."""

        if not isinstance(context, _AITagRunnerFormalizationContext):
            raise TypeError("trusted runner cancellation requires a formalization context")
        nonce = context._signer_nonce  # noqa: SLF001
        with self._lock:
            state = self._contexts.pop(nonce, None)
            if state == "issued":
                context._consumed = True  # noqa: SLF001
                return
            if context._consumed:  # noqa: SLF001
                return
        raise ValueError("runner signing context does not belong to this signer")


def _seal_result_v2(payload: dict[str, object]) -> AITagAnalysisResultV2:
    return seal_payload(
        payload,
        payload_type=_AITagAnalysisResultV2Payload,
        sealed_type=AITagAnalysisResultV2,
        identity_field="result_id",
        identity_prefix="ai-tag-result-v2",
        context="AI Tag Analysis Result V2",
    )


def _seal_outcome_v2(payload: dict[str, object]) -> AITagExecutionOutcomeV2:
    return seal_payload(
        payload,
        payload_type=_AITagExecutionOutcomeV2Payload,
        sealed_type=AITagExecutionOutcomeV2,
        identity_field="outcome_id",
        identity_prefix="ai-tag-outcome-v2",
        context="AI Tag Execution Outcome V2",
    )


def _seal_subject(payload: dict[str, object]) -> AITagTrustedExecutionSubject:
    return seal_payload(
        payload,
        payload_type=_AITagTrustedExecutionSubjectPayload,
        sealed_type=AITagTrustedExecutionSubject,
        identity_field="subject_id",
        identity_prefix="ai-tag-trusted-execution-subject",
        context="AI Tag Trusted Execution Subject",
    )


def _seal_hybrid_v2(payload: dict[str, object]) -> HybridFeatureAnalysisResultV2:
    return seal_payload(
        payload,
        payload_type=_HybridFeatureAnalysisResultV2Payload,
        sealed_type=HybridFeatureAnalysisResultV2,
        identity_field="analysis_id",
        identity_prefix="hybrid-analysis-v2",
        context="Hybrid Feature Analysis Result V2",
    )


def load_ai_tag_analysis_result_v2(raw: str | bytes) -> AITagAnalysisResultV2:
    return load_json_model(raw, AITagAnalysisResultV2, "AI Tag Analysis Result V2")


def load_ai_tag_execution_outcome_v2(raw: str | bytes) -> AITagExecutionOutcomeV2:
    return load_json_model(raw, AITagExecutionOutcomeV2, "AI Tag Execution Outcome V2")


def load_ai_tag_trusted_execution_subject(
    raw: str | bytes,
) -> AITagTrustedExecutionSubject:
    return load_json_model(
        raw,
        AITagTrustedExecutionSubject,
        "AI Tag Trusted Execution Subject",
    )


def load_ai_tag_trusted_runner_attestation(
    raw: str | bytes,
) -> AITagTrustedRunnerAttestation:
    return load_json_model(
        raw,
        AITagTrustedRunnerAttestation,
        "AI Tag Trusted Runner Attestation",
    )


def load_hybrid_feature_analysis_result_v2(
    raw: str | bytes,
) -> HybridFeatureAnalysisResultV2:
    return load_json_model(
        raw,
        HybridFeatureAnalysisResultV2,
        "Hybrid Feature Analysis Result V2",
    )


@dataclass(frozen=True)
class AITagFormalExecutionBundleV2:
    result: AITagAnalysisResultV2 | None
    outcome: AITagExecutionOutcomeV2
    subject: AITagTrustedExecutionSubject
    attestation: AITagTrustedRunnerAttestation
    hybrid: HybridFeatureAnalysisResultV2

    def __post_init__(self) -> None:
        result = (
            None
            if self.result is None
            else AITagAnalysisResultV2.model_validate(self.result.model_dump(mode="json"))
        )
        object.__setattr__(self, "result", result)
        object.__setattr__(
            self,
            "outcome",
            AITagExecutionOutcomeV2.model_validate(self.outcome.model_dump(mode="json")),
        )
        object.__setattr__(
            self,
            "subject",
            AITagTrustedExecutionSubject.model_validate(self.subject.model_dump(mode="json")),
        )
        object.__setattr__(
            self,
            "attestation",
            AITagTrustedRunnerAttestation.model_validate(self.attestation.model_dump(mode="json")),
        )
        object.__setattr__(
            self,
            "hybrid",
            HybridFeatureAnalysisResultV2.model_validate(self.hybrid.model_dump(mode="json")),
        )


_FORMAL_EVIDENCE_TOKEN = object()


class AITagFormalExecutionEvidenceV2(_ImmutableRuntimeObject):
    """Opaque complete verifier input; raw response bytes have no public accessor."""

    __slots__ = (
        "_bundle",
        "_claims",
        "_plan",
        "_raw_response_body",
        "_run_artifacts",
        "_trusted_plan_inputs",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        trusted_plan_inputs: AITagShadowTrustedPlanInputs,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
        run_artifacts: AITagShadowRunArtifacts,
        raw_response_body: bytes | None,
        bundle: AITagFormalExecutionBundleV2,
    ) -> None:
        if construction_token is not _FORMAL_EVIDENCE_TOKEN:
            raise TypeError("formal execution evidence is integrated-runner-owned")
        if not isinstance(trusted_plan_inputs, AITagShadowTrustedPlanInputs):
            raise TypeError("formal evidence requires trusted Plan inputs")
        if not isinstance(run_artifacts, AITagShadowRunArtifacts):
            raise TypeError("formal evidence requires complete shadow run artifacts")
        if not isinstance(bundle, AITagFormalExecutionBundleV2):
            raise TypeError("formal evidence requires a Formal Execution Bundle V2")
        self._trusted_plan_inputs = trusted_plan_inputs
        self._plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        self._claims = AITagShadowDispatchClaims.model_validate(claims.model_dump(mode="json"))
        self._run_artifacts = run_artifacts
        self._raw_response_body = None if raw_response_body is None else bytes(raw_response_body)
        self._bundle = bundle
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return "AITagFormalExecutionEvidenceV2(<complete-evidence-raw-body-redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("formal execution evidence is not serializable")

    @property
    def trusted_plan_inputs(self) -> AITagShadowTrustedPlanInputs:
        return self._trusted_plan_inputs

    @property
    def plan(self) -> AITagShadowDispatchPlan:
        return self._plan

    @property
    def claims(self) -> AITagShadowDispatchClaims:
        return self._claims

    @property
    def run_artifacts(self) -> AITagShadowRunArtifacts:
        return self._run_artifacts

    @property
    def bundle(self) -> AITagFormalExecutionBundleV2:
        return self._bundle


def _verify_trusted_upstream(
    *,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs,
    plan: AITagShadowDispatchPlan,
) -> tuple[
    ReviewUnitAnalysisCard,
    AITagModelView,
    AITagAnalysisRequest,
    VerifiedAITagDispatchEnvelope,
]:
    trusted_plan_inputs.verify_plan(plan)
    card = trusted_plan_inputs.card
    envelope = trusted_plan_inputs.envelope
    model_view = envelope.model_view
    request = envelope.analysis_request
    verify_model_view_against_card_and_policy(
        model_view,
        card,
        policy=DEFAULT_AI_MODEL_VIEW_PROJECTION_POLICY,
    )
    trusted = FullTaxonomyRequestBuilder.default()
    verify_full_taxonomy_request(
        request,
        card=card,
        model_view=model_view,
        feature_config=trusted.feature_config,
        catalog=trusted.catalog,
        prompt=trusted.prompt,
        model_policy=trusted.model_policy,
    )
    verify_ai_tag_dispatch_envelope(
        envelope,
        card=card,
        model_view=model_view,
        request=request,
        feature_config=trusted.feature_config,
        catalog=trusted.catalog,
        prompt=trusted.prompt,
        model_policy=trusted.model_policy,
    )
    return card, model_view, request, envelope


def _formal_status(
    artifacts: AITagShadowRunArtifacts,
) -> tuple[FormalExecutionStatus, str]:
    status = artifacts.observation.status
    if status == "valid_shape":
        return "valid_result", "provider_response_valid"
    if status == "invalid_output":
        return "invalid_output", artifacts.observation.reason_code
    if status in _UNAVAILABLE_REASONS:
        return "unavailable", artifacts.observation.reason_code
    raise ValueError("shadow observation cannot be represented by Formal Outcome V2")


def _build_result_v2(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    artifacts: AITagShadowRunArtifacts,
) -> AITagAnalysisResultV2 | None:
    if artifacts.observation.status != "valid_shape":
        return None
    attempt = artifacts.attempt_receipt
    response = artifacts.provider_response_receipt
    validation = artifacts.response_validation
    if response is None or validation is None:
        raise ValueError("valid formal execution is missing response artifacts")
    if (
        attempt.response_body_sha256 is None
        or attempt.response_body_size_bytes is None
        or response.finish_reason != "stop"
        or validation.status != "valid_shape"
        or validation.model != "deepseek-v4-pro"
    ):
        raise ValueError("valid formal execution has inconsistent response metadata")
    return _seal_result_v2(
        {
            "schema_version": AI_TAG_ANALYSIS_RESULT_V2_SCHEMA_VERSION,
            "request_id": plan.request_id,
            "card_id": plan.card_id,
            "model_view_id": plan.model_view_id,
            "envelope_id": plan.envelope_id,
            "plan_id": plan.plan_id,
            "claims_id": claims.claims_id,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": response.receipt_id,
            "response_validation_id": validation.validation_id,
            "observation_id": artifacts.observation.observation_id,
            "response_body_sha256": attempt.response_body_sha256,
            "response_body_size_bytes": attempt.response_body_size_bytes,
            "provider_response_id": response.provider_response_id,
            "provider": plan.provider,
            "endpoint_url": plan.endpoint_url,
            "model": response.model,
            "system_fingerprint": validation.system_fingerprint,
            "thinking": plan.wire_payload.thinking.type,
            "reasoning_effort": None,
            "response_format": plan.wire_payload.response_format.type,
            "finish_reason": response.finish_reason,
            "judgments": validation.judgments,
            "usage": validation.usage,
            "latency_ms": attempt.latency_ms,
            "attempt_count": 1,
            "output_status": "valid",
            "transport_evidence": "httpx_tls_fixed_endpoint",
            "provider_evidence_scope": "observed_over_tls_not_provider_signed",
            "source_git_provenance_scope": "not_attested",
            "formal_use_scope": "hybrid_retrieval_shadow_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
        }
    )


def _build_outcome_v2(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    artifacts: AITagShadowRunArtifacts,
    result: AITagAnalysisResultV2 | None,
) -> AITagExecutionOutcomeV2:
    status, reason_code = _formal_status(artifacts)
    attempt = artifacts.attempt_receipt
    analysis_run_id = canonical_hash(
        "ai-tag-run-v2",
        {
            "plan_id": plan.plan_id,
            "attempt_receipt_id": attempt.receipt_id,
        },
    )
    return _seal_outcome_v2(
        {
            "schema_version": AI_TAG_EXECUTION_OUTCOME_V2_SCHEMA_VERSION,
            "analysis_run_id": analysis_run_id,
            "card_id": plan.card_id,
            "model_view_id": plan.model_view_id,
            "request_id": plan.request_id,
            "envelope_id": plan.envelope_id,
            "plan_id": plan.plan_id,
            "claims_id": claims.claims_id,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": (
                None
                if artifacts.provider_response_receipt is None
                else artifacts.provider_response_receipt.receipt_id
            ),
            "response_validation_id": (
                None
                if artifacts.response_validation is None
                else artifacts.response_validation.validation_id
            ),
            "outer_diagnostic_id": (
                None
                if artifacts.outer_response_diagnostic is None
                else artifacts.outer_response_diagnostic.diagnostic_id
            ),
            "observation_id": artifacts.observation.observation_id,
            "response_body_sha256": attempt.response_body_sha256,
            "response_body_size_bytes": attempt.response_body_size_bytes,
            "status": status,
            "result_id": None if result is None else result.result_id,
            "reason_code": reason_code,
            "attempt_count": 1,
            "trust_domain_id": claims.trust_domain_id,
            "egress_approval_id": claims.egress_approval_id,
            "budget_reservation_id": claims.budget_reservation_id,
            "credential_scope_id": claims.credential_scope_id,
            "transport_evidence": "httpx_tls_fixed_endpoint",
            "provider_evidence_scope": (
                "http_response_observed_over_tls_not_provider_signed"
                if attempt.transport_status == "response_received"
                else "fixed_tls_transport_attempt_no_complete_verified_response"
            ),
            "formal_use_scope": "hybrid_retrieval_shadow_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
        }
    )


def _build_subject(
    *,
    formalization_event_id: str,
    formalization_started_at: str,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    artifacts: AITagShadowRunArtifacts,
    result: AITagAnalysisResultV2 | None,
    outcome: AITagExecutionOutcomeV2,
    runner_id: str,
    runner_release_fingerprint: str,
    runner_key_id: str,
    runner_registry_id: str,
    registry_policy_fingerprint: str,
) -> AITagTrustedExecutionSubject:
    attempt = artifacts.attempt_receipt
    status, reason_code = _formal_status(artifacts)
    return _seal_subject(
        {
            "schema_version": AI_TAG_TRUSTED_EXECUTION_SUBJECT_SCHEMA_VERSION,
            "formalization_event_id": formalization_event_id,
            "formalization_started_at": formalization_started_at,
            "card_id": plan.card_id,
            "model_view_id": plan.model_view_id,
            "request_id": plan.request_id,
            "envelope_id": plan.envelope_id,
            "plan_id": plan.plan_id,
            "claims_id": claims.claims_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": (
                None
                if artifacts.provider_response_receipt is None
                else artifacts.provider_response_receipt.receipt_id
            ),
            "response_validation_id": (
                None
                if artifacts.response_validation is None
                else artifacts.response_validation.validation_id
            ),
            "outer_diagnostic_id": (
                None
                if artifacts.outer_response_diagnostic is None
                else artifacts.outer_response_diagnostic.diagnostic_id
            ),
            "observation_id": artifacts.observation.observation_id,
            "response_body_sha256": attempt.response_body_sha256,
            "response_body_size_bytes": attempt.response_body_size_bytes,
            "result_id": None if result is None else result.result_id,
            "outcome_id": outcome.outcome_id,
            "formal_status": status,
            "reason_code": reason_code,
            "trust_domain_id": claims.trust_domain_id,
            "egress_approval_id": claims.egress_approval_id,
            "budget_reservation_id": claims.budget_reservation_id,
            "credential_scope_id": claims.credential_scope_id,
            "runner_id": runner_id,
            "runner_release_fingerprint": runner_release_fingerprint,
            "runner_key_id": runner_key_id,
            "runner_registry_id": runner_registry_id,
            "registry_policy_fingerprint": registry_policy_fingerprint,
            "formalization_policy_fingerprint": (AI_TAG_FORMALIZATION_POLICY_FINGERPRINT),
            "provider": plan.provider,
            "endpoint_url": plan.endpoint_url,
            "model": plan.wire_payload.model,
            "transport_evidence": "httpx_tls_fixed_endpoint",
            "network_observation": "observed_by_fixed_httpx_transport",
            "raw_response_rebuild_scope": (
                "passed_complete_http_response"
                if attempt.transport_status == "response_received"
                else "not_applicable_no_complete_http_response"
            ),
            "provider_evidence_scope": (
                "http_response_observed_over_tls_not_provider_signed"
                if attempt.transport_status == "response_received"
                else "fixed_tls_transport_attempt_no_complete_verified_response"
            ),
            "upstream_rebuild_scope": (
                "caller_roots_rebuilt_not_parser_reviewunit_or_git_attestation"
            ),
            "egress_authority_scope": (
                "process_local_verifier_reference_not_external_authority_attestation"
            ),
            "budget_authority_scope": (
                "process_local_ledger_reference_not_production_budget_attestation"
            ),
            "runner_release_scope": (
                "registry_allowlisted_claim_not_code_git_or_remote_attestation"
            ),
            "source_git_provenance_scope": "not_attested",
            "formalization_event_scope": (
                "runner_signed_nonce_not_provider_execution_or_time_authority"
            ),
            "formal_use_scope": "hybrid_retrieval_shadow_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
        }
    )


def _build_hybrid_v2(
    *,
    card: ReviewUnitAnalysisCard,
    request: AITagAnalysisRequest,
    result: AITagAnalysisResultV2 | None,
    outcome: AITagExecutionOutcomeV2,
    subject: AITagTrustedExecutionSubject,
    attestation: AITagTrustedRunnerAttestation,
) -> HybridFeatureAnalysisResultV2:
    decisions = (
        {}
        if result is None
        else {judgment.tag_id: judgment.decision for judgment in result.judgments}
    )
    states: list[HybridTagState] = []
    for contract in request.tag_contract_views:
        tag_id = contract.tag_id
        static_exact: StaticDecision = "positive" if tag_id in card.static_tags.exact else "unknown"
        static_routing: StaticDecision = (
            "positive" if tag_id in card.static_tags.routing else "unknown"
        )
        ai_decision = decisions.get(tag_id)
        states.append(
            HybridTagState(
                tag_id=tag_id,
                static_exact_decision=static_exact,
                static_routing_decision=static_routing,
                ai_unit_decision=ai_decision,
                unit_comparison_status=reduce_unit_comparison(
                    static_exact,
                    ai_decision,
                ),
            )
        )
    diagnostics = () if outcome.status == "valid_result" else (f"ai_execution_{outcome.status}",)
    return _seal_hybrid_v2(
        {
            "schema_version": HYBRID_FEATURE_ANALYSIS_RESULT_V2_SCHEMA_VERSION,
            "unit_id": card.unit_id,
            "card_id": card.card_id,
            "trusted_execution_subject_id": subject.subject_id,
            "trusted_runner_attestation_id": attestation.attestation_id,
            "ai_execution_outcome_id": outcome.outcome_id,
            "ai_result_id": None if result is None else result.result_id,
            "tag_states": states,
            "diagnostics": diagnostics,
            "ai_signal_scope": ("attestation_reference_requires_full_bundle_verification"),
            "formal_review_question_binding": "unchanged_static_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
        }
    )


def _require_fixed_transport(artifacts: AITagShadowRunArtifacts) -> None:
    attempt = artifacts.attempt_receipt
    if (
        attempt.transport_evidence != "httpx_tls_fixed_endpoint"
        or attempt.network_observation != "observed_by_fixed_httpx_transport"
        or attempt.qualification != "local_runtime_observation_not_provider_signature"
    ):
        raise ValueError("formal execution requires the fixed HTTP/TLS transport path")
    response = artifacts.provider_response_receipt
    if response is not None and (
        response.transport_evidence != "httpx_tls_fixed_endpoint"
        or response.qualification != "observed_over_tls_not_provider_signed"
    ):
        raise ValueError("formal execution provider response has untrusted transport scope")


def _produce_ai_tag_formal_execution_v2(
    *,
    capture: _AITagIntegratedExecutionCapture,
    signer: AITagTrustedRunnerSigner,
    registry: AITagTrustedRunnerRegistry,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
) -> AITagFormalExecutionEvidenceV2:
    """Formalize one attempted Plan after mandatory full graph/raw rebuild.

    This is deliberately not a generic signing function. The signer-owned context
    identifies this formalization event, not the provider execution. The existing
    execution graph is fully rebuilt, injected transports are rejected, and the
    produced signature is immediately checked against an externally pinned registry.
    """

    if not isinstance(capture, _AITagIntegratedExecutionCapture):
        raise TypeError("formalization requires an integrated runner capture")
    if type(signer) is not AITagTrustedRunnerSigner:
        raise TypeError("formal producer requires a trusted runner signer")
    if type(registry) is not AITagTrustedRunnerRegistry:
        raise TypeError("formal producer requires a pinned runner registry")
    if (
        signer._runner_registry_id != registry.registry_id  # noqa: SLF001
        or signer._trust_domain_id != registry.trust_domain_id  # noqa: SLF001
        or signer._registry_policy_fingerprint  # noqa: SLF001
        != registry.registry_policy_fingerprint
    ):
        raise ValueError("trusted runner signer differs from pinned registry")
    formalization_context = capture.context
    run_artifacts = capture.run_artifacts
    raw_response_body = capture.raw_response_body
    card, _, request, _ = _verify_trusted_upstream(
        trusted_plan_inputs=trusted_plan_inputs,
        plan=plan,
    )
    verify_deepseek_shadow_run_artifacts(
        run_artifacts,
        plan=plan,
        claims=claims,
        trusted_plan_inputs=trusted_plan_inputs,
        raw_response_body=raw_response_body,
    )
    _require_fixed_transport(run_artifacts)
    if claims.trust_domain_id != registry.trust_domain_id:
        raise ValueError("dispatch Claims differ from trusted runner domain")
    result = _build_result_v2(
        plan=plan,
        claims=claims,
        artifacts=run_artifacts,
    )
    outcome = _build_outcome_v2(
        plan=plan,
        claims=claims,
        artifacts=run_artifacts,
        result=result,
    )
    subject = _build_subject(
        formalization_event_id=(
            formalization_context._formalization_event_id  # noqa: SLF001
        ),
        formalization_started_at=(
            formalization_context._formalization_started_at  # noqa: SLF001
        ),
        plan=plan,
        claims=claims,
        artifacts=run_artifacts,
        result=result,
        outcome=outcome,
        runner_id=signer._runner_id,  # noqa: SLF001
        runner_release_fingerprint=signer._runner_release_fingerprint,  # noqa: SLF001
        runner_key_id=signer._runner_key_id,  # noqa: SLF001
        runner_registry_id=signer._runner_registry_id,  # noqa: SLF001
        registry_policy_fingerprint=signer._registry_policy_fingerprint,  # noqa: SLF001
    )
    attestation = signer._attest_verified_subject(  # noqa: SLF001
        context=formalization_context,
        subject=subject,
    )
    registry.verify(subject=subject, attestation=attestation)
    hybrid = _build_hybrid_v2(
        card=card,
        request=request,
        result=result,
        outcome=outcome,
        subject=subject,
        attestation=attestation,
    )
    bundle = AITagFormalExecutionBundleV2(
        result=result,
        outcome=outcome,
        subject=subject,
        attestation=attestation,
        hybrid=hybrid,
    )
    return AITagFormalExecutionEvidenceV2(
        construction_token=_FORMAL_EVIDENCE_TOKEN,
        trusted_plan_inputs=trusted_plan_inputs,
        plan=plan,
        claims=claims,
        run_artifacts=run_artifacts,
        raw_response_body=raw_response_body,
        bundle=bundle,
    )


class DeepSeekFormalExecutionRunnerV2(_ImmutableRuntimeObject):
    """Run the fixed live transport and immediately formalize its verified bytes.

    The caller supplies dispatch authority but cannot inject transport output or raw
    response bytes. This closes the ordinary runtime hand-off gap; it does not provide
    hardware, remote, provider, source-Git, or deployment-process attestation. The
    signer-holding process and its pinned registry remain explicit trust roots.
    """

    __slots__ = ("_gate", "_registry", "_shadow_runner", "_signer")

    def __init__(
        self,
        *,
        gate: AITagShadowAuthorizationGate,
        signer: AITagTrustedRunnerSigner,
        registry: AITagTrustedRunnerRegistry,
    ) -> None:
        if type(gate) is not AITagShadowAuthorizationGate:
            raise TypeError("formal runner requires the repository authorization gate")
        if type(signer) is not AITagTrustedRunnerSigner:
            raise TypeError("formal runner requires a trusted runner signer")
        if type(registry) is not AITagTrustedRunnerRegistry:
            raise TypeError("formal runner requires a pinned runner registry")
        if (
            gate.trust_domain_id != registry.trust_domain_id
            or signer._runner_registry_id != registry.registry_id  # noqa: SLF001
            or signer._trust_domain_id != registry.trust_domain_id  # noqa: SLF001
            or signer._registry_policy_fingerprint  # noqa: SLF001
            != registry.registry_policy_fingerprint
        ):
            raise ValueError("formal runner trust roots do not agree")
        registry._verify_signer_configuration(signer)  # noqa: SLF001
        self._gate = gate
        self._signer = signer
        self._registry = registry
        # Deliberately construct the runner here without a transport argument. This is
        # the only repository-owned HTTP/TLS path; injected test transports cannot enter.
        self._shadow_runner = DeepSeekShadowRunner(gate=gate)
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return "DeepSeekFormalExecutionRunnerV2(<trusted-live-path>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("formal execution runners are not serializable")

    def run(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
        capability: AITagShadowDispatchCapability,
        envelope: VerifiedAITagDispatchEnvelope,
    ) -> AITagFormalExecutionEvidenceV2:
        # Fail before transport when this immutable registry snapshot rejects the
        # signer key/release. Capability authorization may already have happened.
        self._registry._verify_signer_configuration(self._signer)  # noqa: SLF001
        context = self._signer._begin_formalization(plan)  # noqa: SLF001
        captured: list[_AITagIntegratedExecutionCapture] = []

        def capture_verified_response(
            artifacts: AITagShadowRunArtifacts,
            raw_response_body: bytes | None,
        ) -> None:
            if captured:
                raise RuntimeError("formal runner observed multiple finalized attempts")
            captured.append(
                _AITagIntegratedExecutionCapture(
                    construction_token=_INTEGRATED_CAPTURE_TOKEN,
                    context=context,
                    run_artifacts=artifacts,
                    raw_response_body=raw_response_body,
                )
            )

        try:
            run_artifacts = self._shadow_runner.run(
                plan=plan,
                claims=claims,
                capability=capability,
                envelope=envelope,
                _verified_raw_response_sink=capture_verified_response,
            )
            if len(captured) != 1 or captured[0].run_artifacts is not run_artifacts:
                raise RuntimeError("formal runner did not capture its finalized attempt")
            return _produce_ai_tag_formal_execution_v2(
                capture=captured[0],
                signer=self._signer,
                registry=self._registry,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                plan=plan,
                claims=claims,
            )
        finally:
            self._signer._cancel_formalization(context)  # noqa: SLF001


_ELIGIBILITY_TOKEN = object()


class VerifiedAITagFormalExecutionEligibility(_ImmutableRuntimeObject):
    """Opaque runtime proof returned only after registry and evidence verification."""

    __slots__ = (
        "_attestation_id",
        "_card_id",
        "_construction_token",
        "_hybrid",
        "_outcome",
        "_positive_tags",
        "_result",
        "_subject_id",
        "_unit_id",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        bundle: AITagFormalExecutionBundleV2,
    ) -> None:
        if construction_token is not _ELIGIBILITY_TOKEN:
            raise TypeError("formal eligibility can only be issued by the trusted verifier")
        self._construction_token = construction_token
        self._unit_id = bundle.hybrid.unit_id
        self._card_id = bundle.hybrid.card_id
        self._subject_id = bundle.subject.subject_id
        self._attestation_id = bundle.attestation.attestation_id
        self._outcome = bundle.outcome
        self._result = bundle.result
        self._hybrid = bundle.hybrid
        self._positive_tags = (
            ()
            if bundle.result is None
            else tuple(
                judgment.tag_id
                for judgment in bundle.result.judgments
                if judgment.decision == "positive"
            )
        )
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return "VerifiedAITagFormalExecutionEligibility(<opaque-verified-proof>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("formal execution eligibility is not serializable")

    @property
    def unit_id(self) -> str:
        return self._unit_id

    @property
    def card_id(self) -> str:
        return self._card_id

    @property
    def subject_id(self) -> str:
        return self._subject_id

    @property
    def attestation_id(self) -> str:
        return self._attestation_id

    @property
    def outcome(self) -> AITagExecutionOutcomeV2:
        return self._outcome

    @property
    def result(self) -> AITagAnalysisResultV2 | None:
        return self._result

    @property
    def hybrid(self) -> HybridFeatureAnalysisResultV2:
        return self._hybrid

    @property
    def positive_tags(self) -> tuple[str, ...]:
        return self._positive_tags


class AITagFormalExecutionVerifierV2(_ImmutableRuntimeObject):
    """Long-lived verifier configured with one externally pinned registry."""

    __slots__ = ("_registry",)

    def __init__(self, *, registry: AITagTrustedRunnerRegistry) -> None:
        if type(registry) is not AITagTrustedRunnerRegistry:
            raise TypeError("formal verifier requires a pinned runner registry")
        registry._verify_registry_integrity()  # noqa: SLF001
        self._registry = registry
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return f"AITagFormalExecutionVerifierV2(registry_id={self._registry.registry_id!r})"

    def __reduce__(self) -> NoReturn:
        raise TypeError("formal execution verifiers are runtime roots and are not serializable")

    @property
    def registry_id(self) -> str:
        return self._registry.registry_id

    def verify(
        self,
        evidence: AITagFormalExecutionEvidenceV2,
    ) -> VerifiedAITagFormalExecutionEligibility:
        if type(evidence) is not AITagFormalExecutionEvidenceV2:
            raise TypeError("formal verifier requires complete Formal Execution Evidence V2")
        self._registry._verify_registry_integrity()  # noqa: SLF001
        plan = AITagShadowDispatchPlan.model_validate(evidence.plan.model_dump(mode="json"))
        claims = AITagShadowDispatchClaims.model_validate(evidence.claims.model_dump(mode="json"))
        card, _, request, _ = _verify_trusted_upstream(
            trusted_plan_inputs=evidence.trusted_plan_inputs,
            plan=plan,
        )
        verify_deepseek_shadow_run_artifacts(
            evidence.run_artifacts,
            plan=plan,
            claims=claims,
            trusted_plan_inputs=evidence.trusted_plan_inputs,
            raw_response_body=evidence._raw_response_body,  # noqa: SLF001
        )
        _require_fixed_transport(evidence.run_artifacts)
        bundle = evidence.bundle
        subject = AITagTrustedExecutionSubject.model_validate(
            bundle.subject.model_dump(mode="json")
        )
        expected_result = _build_result_v2(
            plan=plan,
            claims=claims,
            artifacts=evidence.run_artifacts,
        )
        if expected_result != bundle.result:
            raise ValueError("Result V2 differs from deterministic evidence projection")
        expected_outcome = _build_outcome_v2(
            plan=plan,
            claims=claims,
            artifacts=evidence.run_artifacts,
            result=expected_result,
        )
        if expected_outcome != bundle.outcome:
            raise ValueError("Outcome V2 differs from deterministic evidence projection")
        expected_subject = _build_subject(
            formalization_event_id=subject.formalization_event_id,
            formalization_started_at=subject.formalization_started_at,
            plan=plan,
            claims=claims,
            artifacts=evidence.run_artifacts,
            result=expected_result,
            outcome=expected_outcome,
            runner_id=subject.runner_id,
            runner_release_fingerprint=subject.runner_release_fingerprint,
            runner_key_id=subject.runner_key_id,
            runner_registry_id=subject.runner_registry_id,
            registry_policy_fingerprint=subject.registry_policy_fingerprint,
        )
        if expected_subject != subject:
            raise ValueError("trusted execution subject differs from deterministic projection")
        self._registry.verify(
            subject=subject,
            attestation=bundle.attestation,
        )
        expected_hybrid = _build_hybrid_v2(
            card=card,
            request=request,
            result=expected_result,
            outcome=expected_outcome,
            subject=subject,
            attestation=bundle.attestation,
        )
        if expected_hybrid != bundle.hybrid:
            raise ValueError("Hybrid V2 differs from verified formal execution projection")
        return VerifiedAITagFormalExecutionEligibility(
            construction_token=_ELIGIBILITY_TOKEN,
            bundle=bundle,
        )


__all__ = [
    "AI_TAG_ANALYSIS_RESULT_V2_SCHEMA_VERSION",
    "AI_TAG_EXECUTION_OUTCOME_V2_SCHEMA_VERSION",
    "AI_TAG_FORMALIZATION_POLICY_FINGERPRINT",
    "AI_TAG_TRUSTED_EXECUTION_SUBJECT_SCHEMA_VERSION",
    "AI_TAG_TRUSTED_RUNNER_ATTESTATION_SCHEMA_VERSION",
    "HYBRID_FEATURE_ANALYSIS_RESULT_V2_SCHEMA_VERSION",
    "AITagAnalysisResultV2",
    "AITagExecutionOutcomeV2",
    "AITagFormalExecutionBundleV2",
    "AITagFormalExecutionEvidenceV2",
    "AITagFormalExecutionVerifierV2",
    "AITagTrustedExecutionSubject",
    "AITagTrustedRunnerAttestation",
    "AITagTrustedRunnerKeyRecord",
    "AITagTrustedRunnerRegistry",
    "AITagTrustedRunnerSigner",
    "DeepSeekFormalExecutionRunnerV2",
    "HybridFeatureAnalysisResultV2",
    "VerifiedAITagFormalExecutionEligibility",
    "build_ai_tag_trusted_runner_key_record",
    "compute_ai_tag_runner_registry_id",
    "load_ai_tag_analysis_result_v2",
    "load_ai_tag_execution_outcome_v2",
    "load_ai_tag_trusted_execution_subject",
    "load_ai_tag_trusted_runner_attestation",
    "load_hybrid_feature_analysis_result_v2",
    "new_ai_tag_formalization_event_id",
    "runner_key_id_from_public_key",
]
