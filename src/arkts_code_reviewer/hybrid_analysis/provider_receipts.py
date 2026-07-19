from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.builders import AnalysisContextPolicy
from arkts_code_reviewer.hybrid_analysis.dispatch import (
    DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
    DeepSeekChatMessage,
    DeepSeekResponseFormat,
    DeepSeekThinking,
    VerifiedAITagDispatchEnvelope,
)
from arkts_code_reviewer.hybrid_analysis.execution import AITagRawUsage
from arkts_code_reviewer.hybrid_analysis.models import (
    ReviewUnitAnalysisCard,
    verify_model_view_against_card,
)

AI_TAG_SHADOW_DISPATCH_PLAN_SCHEMA_VERSION = "ai-tag-shadow-dispatch-plan-v1"
AI_TAG_SHADOW_PROVIDER_POLICY_SCHEMA_VERSION = "ai-tag-shadow-provider-policy-v1"
AI_TAG_SHADOW_DISPATCH_CLAIMS_SCHEMA_VERSION = "ai-tag-shadow-dispatch-claims-v1"
AI_TAG_DISPATCH_ATTEMPT_RECEIPT_SCHEMA_VERSION = "ai-tag-dispatch-attempt-receipt-v1"
AI_TAG_OBSERVED_RESPONSE_RECEIPT_SCHEMA_VERSION = "ai-tag-observed-response-receipt-v1"
AI_TAG_OBSERVED_RESPONSE_RECEIPT_V2_SCHEMA_VERSION = "ai-tag-observed-response-receipt-v2"
AI_TAG_SHADOW_EXECUTION_OBSERVATION_SCHEMA_VERSION = "ai-tag-shadow-execution-observation-v1"
AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION = "ai-tag-shadow-execution-observation-v2"

_HASH = r"[0-9a-f]{64}"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_SHADOW_POLICY_FINGERPRINT = rf"^ai-tag-shadow-policy:sha256:{_HASH}$"
_MODEL_POLICY_FINGERPRINT = rf"^ai-tag-policy:sha256:{_HASH}$"
_CLAIMS_ID = rf"^ai-tag-shadow-claims:sha256:{_HASH}$"
_ATTEMPT_RECEIPT_ID = rf"^ai-tag-attempt-receipt:sha256:{_HASH}$"
_RESPONSE_RECEIPT_ID = rf"^ai-tag-observed-response:sha256:{_HASH}$"
_OBSERVATION_ID = rf"^ai-tag-shadow-observation:sha256:{_HASH}$"
_OUTER_DIAGNOSTIC_ID = rf"^deepseek-outer-response-diagnostic:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_CONTEXT_POLICY_FINGERPRINT = rf"^analysis-context-policy:sha256:{_HASH}$"
_TRUST_DOMAIN_ID = rf"^ai-shadow-trust-domain:sha256:{_HASH}$"
_EGRESS_APPROVAL_ID = rf"^ai-egress-approval:sha256:{_HASH}$"
_BUDGET_RESERVATION_ID = rf"^ai-budget-reservation:sha256:{_HASH}$"
_CREDENTIAL_SCOPE_ID = rf"^deepseek-credential-scope:sha256:{_HASH}$"
_VALIDATION_ID = rf"^ai-tag-response-validation:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"


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


class DeepSeekShadowChatPayload(FrozenModel):
    """The exact no-secret JSON body eligible for one shadow attempt."""

    model: Literal["deepseek-v4-pro"]
    messages: Annotated[tuple[DeepSeekChatMessage, ...], Field(min_length=2, max_length=2)]
    thinking: DeepSeekThinking
    temperature: int
    stream: Literal[False]
    tool_choice: Literal["none"]
    response_format: DeepSeekResponseFormat
    max_tokens: Annotated[int, Field(ge=256, le=16_384)]

    @field_validator("messages", mode="before")
    @classmethod
    def parse_messages(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DeepSeekShadowChatPayload.messages")

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature(cls, value: object) -> object:
        if type(value) is not int or value != 0:
            raise ValueError("DeepSeekShadowChatPayload.temperature must be the integer 0")
        return value

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        if tuple(message.role for message in self.messages) != ("system", "user"):
            raise ValueError("DeepSeek shadow messages must be system then user")
        return self


class _AITagShadowProviderPolicyPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-provider-policy-v1"]
    policy_version: Literal["deepseek-shadow-provider-policy-v2"]
    upstream_render_policy_fingerprint: Annotated[
        str,
        Field(pattern=_MODEL_POLICY_FINGERPRINT),
    ]
    upstream_dispatch_mode_required: Literal["disabled_no_budget_no_approval"]
    provider: Literal["deepseek"]
    provider_contract_snapshot: Literal[
        "deepseek-chat-completions-2026-07-18",
        "deepseek-chat-completions-2026-07-18-r2",
        "deepseek-chat-completions-2026-07-19-r3",
    ]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    thinking: Literal["disabled"]
    response_format: Literal["json_object"]
    dispatch_mode: Literal["shadow_runtime_authorization_required"]
    egress_builder_version: Literal["analysis-card-builder-v2-provider-egress"]
    egress_policy: Literal["none_requires_exact_body_runtime_approval"]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=120_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    retry_policy: Literal["none_single_attempt_v1"]
    tls_verify: Literal[True]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    qualification: Literal["development_shadow_not_production_approved"]


class AITagShadowProviderPolicy(_AITagShadowProviderPolicyPayload):
    """Versioned policy that does not mutate or enable the render-only v1 policy."""

    policy_fingerprint: Annotated[str, Field(pattern=_SHADOW_POLICY_FINGERPRINT)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-policy",
            identity_payload(self, "policy_fingerprint"),
        )
        if self.policy_fingerprint != expected:
            raise ValueError("shadow provider policy fingerprint does not match its contents")
        return self


def seal_ai_tag_shadow_provider_policy(
    payload: Mapping[str, object],
) -> AITagShadowProviderPolicy:
    return seal_payload(
        payload,
        payload_type=_AITagShadowProviderPolicyPayload,
        sealed_type=AITagShadowProviderPolicy,
        identity_field="policy_fingerprint",
        identity_prefix="ai-tag-shadow-policy",
        context="AI Tag Shadow Provider Policy",
    )


class _AITagShadowDispatchPlanPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-dispatch-plan-v1"]
    plan_version: Literal["deepseek-shadow-single-attempt-v1"]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    source_context_policy_fingerprint: Annotated[
        str,
        Field(pattern=_CONTEXT_POLICY_FINGERPRINT),
    ]
    source_egress_state: Literal["no_redaction_exact_body_runtime_approval_required"]
    shadow_provider_policy: AITagShadowProviderPolicy
    provider: Literal["deepseek"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    http_method: Literal["POST"]
    execution_mode: Literal["shadow_only_no_hybrid_no_retrieval"]
    wire_payload: DeepSeekShadowChatPayload
    wire_body_json: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=120_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    tls_verify: Literal[True]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    qualification: Literal["plan_not_authorization"]

    @model_validator(mode="after")
    def validate_wire_identity(self) -> Self:
        expected_body = canonical_json(self.wire_payload.model_dump(mode="json"))
        if self.wire_body_json != expected_body:
            raise ValueError("shadow dispatch plan wire JSON is not canonical or bound")
        expected_hash = f"sha256:{hashlib.sha256(expected_body.encode('utf-8')).hexdigest()}"
        if self.wire_body_sha256 != expected_hash:
            raise ValueError("shadow dispatch plan wire body hash does not match")
        policy = self.shadow_provider_policy
        if (
            self.provider != policy.provider
            or self.endpoint_url != policy.endpoint_url
            or self.wire_payload.model != policy.model
            or self.wire_payload.thinking.type != policy.thinking
            or self.wire_payload.response_format.type != policy.response_format
            or self.wire_payload.max_tokens != policy.max_output_tokens
            or self.wall_clock_timeout_ms != policy.wall_clock_timeout_ms
            or self.max_response_bytes != policy.max_response_bytes
            or self.max_attempts != policy.max_attempts
            or self.tls_verify != policy.tls_verify
            or self.follow_redirects != policy.follow_redirects
            or self.trust_env != policy.trust_env
        ):
            raise ValueError("shadow dispatch plan differs from its provider policy")
        return self


class AITagShadowDispatchPlan(_AITagShadowDispatchPlanPayload):
    """A deterministic send plan. It is deliberately not an authorization."""

    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-plan",
            identity_payload(self, "plan_id"),
        )
        if self.plan_id != expected:
            raise ValueError("shadow dispatch plan ID does not match its contents")
        return self


def seal_ai_tag_shadow_dispatch_plan(
    payload: Mapping[str, object],
) -> AITagShadowDispatchPlan:
    return seal_payload(
        payload,
        payload_type=_AITagShadowDispatchPlanPayload,
        sealed_type=AITagShadowDispatchPlan,
        identity_field="plan_id",
        identity_prefix="ai-tag-shadow-plan",
        context="AI Tag Shadow Dispatch Plan",
    )


def load_ai_tag_shadow_dispatch_plan(raw: str | bytes) -> AITagShadowDispatchPlan:
    return load_json_model(raw, AITagShadowDispatchPlan, "AI Tag Shadow Dispatch Plan")


def build_ai_tag_shadow_dispatch_plan(
    *,
    envelope: VerifiedAITagDispatchEnvelope,
    card: ReviewUnitAnalysisCard,
    context_policy: AnalysisContextPolicy,
    max_output_tokens: int,
    timeout_ms: int = 60_000,
    max_response_bytes: int = 2_000_000,
) -> AITagShadowDispatchPlan:
    envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    card = ReviewUnitAnalysisCard.model_validate(card.model_dump(mode="json"))
    verify_model_view_against_card(envelope.model_view, card)
    if envelope.analysis_request.card_id != card.card_id:
        raise ValueError("dispatch envelope Request refers to a different Card")
    if card.context_policy_fingerprint != context_policy.fingerprint:
        raise ValueError("Analysis Card differs from the supplied context policy")
    if context_policy.redaction_policy != "none_requires_exact_body_runtime_approval":
        raise ValueError("Analysis Card policy does not permit provider egress planning")
    if context_policy.builder_version != "analysis-card-builder-v2-provider-egress":
        raise ValueError("Analysis Card builder is not the provider-egress version")
    if envelope.model_policy.dispatch_mode != "disabled_no_budget_no_approval":
        raise ValueError("upstream render policy is not the frozen no-dispatch policy")
    shadow_policy = seal_ai_tag_shadow_provider_policy(
        {
            "schema_version": AI_TAG_SHADOW_PROVIDER_POLICY_SCHEMA_VERSION,
            "policy_version": "deepseek-shadow-provider-policy-v2",
            "upstream_render_policy_fingerprint": (envelope.model_policy.model_policy_fingerprint),
            "upstream_dispatch_mode_required": "disabled_no_budget_no_approval",
            "provider": "deepseek",
            "provider_contract_snapshot": "deepseek-chat-completions-2026-07-19-r3",
            "endpoint_url": DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
            "model": envelope.model_policy.model,
            "thinking": envelope.model_policy.thinking,
            "response_format": envelope.model_policy.response_format,
            "dispatch_mode": "shadow_runtime_authorization_required",
            "egress_builder_version": context_policy.builder_version,
            "egress_policy": context_policy.redaction_policy,
            "max_output_tokens": max_output_tokens,
            "wall_clock_timeout_ms": timeout_ms,
            "max_response_bytes": max_response_bytes,
            "max_attempts": 1,
            "retry_policy": "none_single_attempt_v1",
            "tls_verify": True,
            "follow_redirects": False,
            "trust_env": False,
            "qualification": "development_shadow_not_production_approved",
        }
    )
    base_wire = envelope.wire_payload
    wire_payload = DeepSeekShadowChatPayload(
        model=base_wire.model,
        messages=base_wire.messages,
        thinking=base_wire.thinking,
        temperature=base_wire.temperature,
        stream=base_wire.stream,
        tool_choice=base_wire.tool_choice,
        response_format=base_wire.response_format,
        max_tokens=max_output_tokens,
    )
    wire_body_json = canonical_json(wire_payload.model_dump(mode="json"))
    return seal_ai_tag_shadow_dispatch_plan(
        {
            "schema_version": AI_TAG_SHADOW_DISPATCH_PLAN_SCHEMA_VERSION,
            "plan_version": "deepseek-shadow-single-attempt-v1",
            "envelope_id": envelope.envelope_id,
            "request_id": envelope.analysis_request.request_id,
            "card_id": card.card_id,
            "model_view_id": envelope.model_view.model_view_id,
            "source_context_policy_fingerprint": card.context_policy_fingerprint,
            "source_egress_state": ("no_redaction_exact_body_runtime_approval_required"),
            "shadow_provider_policy": shadow_policy,
            "provider": "deepseek",
            "endpoint_url": DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
            "http_method": "POST",
            "execution_mode": "shadow_only_no_hybrid_no_retrieval",
            "wire_payload": wire_payload,
            "wire_body_json": wire_body_json,
            "wire_body_sha256": (
                "sha256:" + hashlib.sha256(wire_body_json.encode("utf-8")).hexdigest()
            ),
            "wall_clock_timeout_ms": timeout_ms,
            "max_response_bytes": max_response_bytes,
            "max_attempts": 1,
            "tls_verify": True,
            "follow_redirects": False,
            "trust_env": False,
            "qualification": "plan_not_authorization",
        }
    )


def verify_ai_tag_shadow_dispatch_plan(
    plan: AITagShadowDispatchPlan,
    *,
    envelope: VerifiedAITagDispatchEnvelope,
    card: ReviewUnitAnalysisCard,
    context_policy: AnalysisContextPolicy,
    trusted_max_output_tokens: int,
    trusted_timeout_ms: int,
    trusted_max_response_bytes: int,
) -> None:
    plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
    expected = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=context_policy,
        max_output_tokens=trusted_max_output_tokens,
        timeout_ms=trusted_timeout_ms,
        max_response_bytes=trusted_max_response_bytes,
    )
    if plan != expected:
        raise ValueError("shadow dispatch plan differs from deterministic rebuild")


class _AITagShadowDispatchClaimsPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-dispatch-claims-v1"]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    trust_domain_id: Annotated[str, Field(pattern=_TRUST_DOMAIN_ID)]
    egress_approval_id: Annotated[str, Field(pattern=_EGRESS_APPROVAL_ID)]
    budget_reservation_id: Annotated[str, Field(pattern=_BUDGET_RESERVATION_ID)]
    credential_scope_id: Annotated[str, Field(pattern=_CREDENTIAL_SCOPE_ID)]
    egress_scope: Literal["exact_wire_body_sha256"]
    budget_scope: Literal["one_attempt_worst_case_reserved"]
    qualification: Literal["references_require_runtime_verification"]


class AITagShadowDispatchClaims(_AITagShadowDispatchClaimsPayload):
    """Serializable references; not an approval and not a dispatch capability."""

    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-claims",
            identity_payload(self, "claims_id"),
        )
        if self.claims_id != expected:
            raise ValueError("shadow dispatch claims ID does not match its contents")
        return self


def seal_ai_tag_shadow_dispatch_claims(
    payload: Mapping[str, object],
) -> AITagShadowDispatchClaims:
    return seal_payload(
        payload,
        payload_type=_AITagShadowDispatchClaimsPayload,
        sealed_type=AITagShadowDispatchClaims,
        identity_field="claims_id",
        identity_prefix="ai-tag-shadow-claims",
        context="AI Tag Shadow Dispatch Claims",
    )


def load_ai_tag_shadow_dispatch_claims(raw: str | bytes) -> AITagShadowDispatchClaims:
    return load_json_model(raw, AITagShadowDispatchClaims, "AI Tag Shadow Dispatch Claims")


AttemptTransportStatus = Literal[
    "response_received",
    "provider_timeout",
    "provider_transport_error",
    "provider_response_too_large",
]


class _AITagDispatchAttemptReceiptPayload(FrozenModel):
    schema_version: Literal["ai-tag-dispatch-attempt-receipt-v1"]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    trust_domain_id: Annotated[str, Field(pattern=_TRUST_DOMAIN_ID)]
    egress_approval_id: Annotated[str, Field(pattern=_EGRESS_APPROVAL_ID)]
    budget_reservation_id: Annotated[str, Field(pattern=_BUDGET_RESERVATION_ID)]
    credential_scope_id: Annotated[str, Field(pattern=_CREDENTIAL_SCOPE_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    http_method: Literal["POST"]
    attempt_ordinal: Literal[1]
    tls_verify: Literal[True]
    follow_redirects: Literal[False]
    trust_env: Literal[False]
    transport_evidence: Literal[
        "httpx_tls_fixed_endpoint",
        "injected_untrusted_transport",
    ]
    network_observation: Literal[
        "observed_by_fixed_httpx_transport",
        "not_established_by_injected_transport",
    ]
    transport_status: AttemptTransportStatus
    http_status: Annotated[int | None, Field(ge=100, le=599)]
    response_body_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int | None, Field(ge=0, le=8_000_000)]
    retry_after_ms: Annotated[int | None, Field(ge=0, le=120_000)]
    latency_ms: Annotated[int, Field(ge=0)]
    qualification: Literal[
        "local_runtime_observation_not_provider_signature",
        "synthetic_or_untrusted_transport_not_network_evidence",
    ]

    @model_validator(mode="after")
    def validate_transport_matrix(self) -> Self:
        if self.transport_evidence == "httpx_tls_fixed_endpoint":
            if (
                self.network_observation != "observed_by_fixed_httpx_transport"
                or self.qualification != "local_runtime_observation_not_provider_signature"
            ):
                raise ValueError("live httpx attempt uses inconsistent evidence labels")
        elif (
            self.network_observation != "not_established_by_injected_transport"
            or self.qualification != "synthetic_or_untrusted_transport_not_network_evidence"
        ):
            raise ValueError("injected attempt uses inconsistent evidence labels")
        response_values = (
            self.http_status,
            self.response_body_sha256,
            self.response_body_size_bytes,
        )
        if self.transport_status == "response_received":
            if any(value is None for value in response_values):
                raise ValueError("response receipt requires status, body hash, and body size")
        elif any(value is not None for value in response_values):
            raise ValueError("transport failure cannot claim a complete HTTP response")
        if self.retry_after_ms is not None and self.http_status != 429:
            raise ValueError("Retry-After is only retained for HTTP 429")
        return self


class AITagDispatchAttemptReceipt(_AITagDispatchAttemptReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-attempt-receipt",
            identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("attempt receipt ID does not match its contents")
        return self


def seal_ai_tag_dispatch_attempt_receipt(
    payload: Mapping[str, object],
) -> AITagDispatchAttemptReceipt:
    return seal_payload(
        payload,
        payload_type=_AITagDispatchAttemptReceiptPayload,
        sealed_type=AITagDispatchAttemptReceipt,
        identity_field="receipt_id",
        identity_prefix="ai-tag-attempt-receipt",
        context="AI Tag Dispatch Attempt Receipt",
    )


def load_ai_tag_dispatch_attempt_receipt(raw: str | bytes) -> AITagDispatchAttemptReceipt:
    return load_json_model(raw, AITagDispatchAttemptReceipt, "AI Tag Dispatch Attempt Receipt")


class _AITagObservedProviderResponseReceiptFields(FrozenModel):
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    http_status: Literal[200]
    response_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    response_body_size_bytes: Annotated[int, Field(ge=1, le=8_000_000)]
    provider_response_id: Annotated[str, Field(min_length=1, max_length=500)]
    response_object: Literal["chat.completion"]
    created: Annotated[int, Field(ge=0)]
    model: Literal["deepseek-v4-pro"]
    system_fingerprint: Annotated[str | None, Field(max_length=500)]
    choice_count: Literal[1]
    selected_choice_index: Literal[0]
    message_role: Literal["assistant"]
    finish_reason: Literal[
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
    ]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    usage: AITagRawUsage | None
    transport_evidence: Literal[
        "httpx_tls_fixed_endpoint",
        "injected_untrusted_transport",
    ]
    qualification: Literal[
        "observed_over_tls_not_provider_signed",
        "synthetic_or_untrusted_transport_not_provider_observation",
    ]

    @field_validator("provider_response_id")
    @classmethod
    def validate_provider_response_id(cls, value: str) -> str:
        return _single_line(value, "provider_response_id")

    @field_validator("system_fingerprint")
    @classmethod
    def validate_system_fingerprint(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "system_fingerprint", max_length=500)

    @model_validator(mode="after")
    def validate_evidence_label(self) -> Self:
        expected = (
            "observed_over_tls_not_provider_signed"
            if self.transport_evidence == "httpx_tls_fixed_endpoint"
            else "synthetic_or_untrusted_transport_not_provider_observation"
        )
        if self.qualification != expected:
            raise ValueError("observed response qualification differs from transport evidence")
        return self


class _AITagObservedProviderResponseReceiptPayload(
    _AITagObservedProviderResponseReceiptFields
):
    schema_version: Literal["ai-tag-observed-response-receipt-v1"]


class AITagObservedProviderResponseReceipt(_AITagObservedProviderResponseReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_RESPONSE_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-observed-response",
            identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("observed response receipt ID does not match its contents")
        return self


def seal_ai_tag_observed_provider_response_receipt(
    payload: Mapping[str, object],
) -> AITagObservedProviderResponseReceipt:
    return seal_payload(
        payload,
        payload_type=_AITagObservedProviderResponseReceiptPayload,
        sealed_type=AITagObservedProviderResponseReceipt,
        identity_field="receipt_id",
        identity_prefix="ai-tag-observed-response",
        context="AI Tag Observed Provider Response Receipt",
    )


def load_ai_tag_observed_provider_response_receipt(
    raw: str | bytes,
) -> AITagObservedProviderResponseReceipt:
    return load_json_model(
        raw,
        AITagObservedProviderResponseReceipt,
        "AI Tag Observed Provider Response Receipt",
    )


class _AITagObservedProviderResponseReceiptV2Payload(
    _AITagObservedProviderResponseReceiptFields
):
    """Current receipt shape for the bounded usage-extension compatibility policy."""

    schema_version: Literal["ai-tag-observed-response-receipt-v2"]
    provider_contract_snapshot: Literal["deepseek-chat-completions-2026-07-19-r3"]
    outer_parser_contract_version: Literal["deepseek-outer-response-parser-v2"]
    usage_extension_policy: Literal["direct_unknown_usage_fields_discarded-v1"]
    ignored_usage_extension_count: Annotated[int, Field(ge=0, le=16)]
    usage_extension_disposition: Literal[
        "none_observed",
        "discarded_without_name_or_value_retention",
    ]

    @model_validator(mode="after")
    def validate_usage_extension_disposition(self) -> Self:
        expected = (
            "none_observed"
            if self.ignored_usage_extension_count == 0
            else "discarded_without_name_or_value_retention"
        )
        if self.usage_extension_disposition != expected:
            raise ValueError("usage extension count and disposition differ")
        if self.ignored_usage_extension_count > 0 and self.usage is None:
            raise ValueError("ignored usage extensions require parsed known usage")
        if self.usage is not None and (
            self.usage.prompt_tokens is None or self.usage.completion_tokens is None
        ):
            raise ValueError("observed response receipt V2 usage requires token totals")
        return self


class AITagObservedProviderResponseReceiptV2(
    _AITagObservedProviderResponseReceiptV2Payload
):
    receipt_id: Annotated[str, Field(pattern=_RESPONSE_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-observed-response",
            identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("observed response receipt V2 ID does not match its contents")
        return self


def seal_ai_tag_observed_provider_response_receipt_v2(
    payload: Mapping[str, object],
) -> AITagObservedProviderResponseReceiptV2:
    return seal_payload(
        payload,
        payload_type=_AITagObservedProviderResponseReceiptV2Payload,
        sealed_type=AITagObservedProviderResponseReceiptV2,
        identity_field="receipt_id",
        identity_prefix="ai-tag-observed-response",
        context="AI Tag Observed Provider Response Receipt V2",
    )


def load_ai_tag_observed_provider_response_receipt_v2(
    raw: str | bytes,
) -> AITagObservedProviderResponseReceiptV2:
    return load_json_model(
        raw,
        AITagObservedProviderResponseReceiptV2,
        "AI Tag Observed Provider Response Receipt V2",
    )


ShadowObservationStatus = Literal[
    "valid_shape",
    "invalid_output",
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
    "provider_transport_error",
    "provider_response_too_large",
]


class _AITagShadowExecutionObservationPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-execution-observation-v1"]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[
        str | None,
        Field(pattern=_RESPONSE_RECEIPT_ID),
    ]
    response_validation_id: Annotated[str | None, Field(pattern=_VALIDATION_ID)]
    status: ShadowObservationStatus
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]
    qualification: Literal["unattested_shadow_not_formal"]

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        value = _single_line(value, "reason_code", max_length=100)
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("reason_code must be lowercase alphanumeric snake case")
        return value

    @model_validator(mode="after")
    def validate_observation_matrix(self) -> Self:
        provider_failures = {
            "provider_client_error",
            "provider_rate_limited",
            "provider_server_error",
            "provider_timeout",
            "provider_transport_error",
            "provider_response_too_large",
        }
        if self.status == "valid_shape":
            if (
                self.provider_response_receipt_id is None
                or self.response_validation_id is None
                or self.reason_code != "response_shape_valid"
            ):
                raise ValueError("valid shadow observation requires response and validation")
        elif self.status == "invalid_output":
            artifacts_present = (
                self.provider_response_receipt_id is not None,
                self.response_validation_id is not None,
            )
            if artifacts_present == (False, False):
                if self.reason_code != "provider_outer_contract_invalid":
                    raise ValueError("outer-invalid observation uses an invalid reason")
            elif artifacts_present != (True, True):
                raise ValueError("inner-invalid observation requires both response artifacts")
        elif self.status in provider_failures:
            if self.reason_code != self.status:
                raise ValueError("provider failure observation reason must equal status")
            if (
                self.provider_response_receipt_id is not None
                or self.response_validation_id is not None
            ):
                raise ValueError("provider failure cannot carry response artifacts")
        elif (
            self.provider_response_receipt_id is not None or self.response_validation_id is not None
        ):
            raise ValueError("provider failure cannot carry response validation artifacts")
        return self


class AITagShadowExecutionObservation(_AITagShadowExecutionObservationPayload):
    observation_id: Annotated[str, Field(pattern=_OBSERVATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-observation",
            identity_payload(self, "observation_id"),
        )
        if self.observation_id != expected:
            raise ValueError("shadow observation ID does not match its contents")
        return self


def seal_ai_tag_shadow_execution_observation(
    payload: Mapping[str, object],
) -> AITagShadowExecutionObservation:
    return seal_payload(
        payload,
        payload_type=_AITagShadowExecutionObservationPayload,
        sealed_type=AITagShadowExecutionObservation,
        identity_field="observation_id",
        identity_prefix="ai-tag-shadow-observation",
        context="AI Tag Shadow Execution Observation",
    )


def load_ai_tag_shadow_execution_observation(
    raw: str | bytes,
) -> AITagShadowExecutionObservation:
    return load_json_model(
        raw,
        AITagShadowExecutionObservation,
        "AI Tag Shadow Execution Observation",
    )


class _AITagShadowExecutionObservationV2Payload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-execution-observation-v2"]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    attempt_receipt_id: Annotated[str, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[
        str | None,
        Field(pattern=_RESPONSE_RECEIPT_ID),
    ]
    response_validation_id: Annotated[str | None, Field(pattern=_VALIDATION_ID)]
    outer_diagnostic_id: Annotated[str | None, Field(pattern=_OUTER_DIAGNOSTIC_ID)]
    status: ShadowObservationStatus
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]
    qualification: Literal["unattested_shadow_not_formal"]

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        value = _single_line(value, "reason_code", max_length=100)
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("reason_code must be lowercase alphanumeric snake case")
        return value

    @model_validator(mode="after")
    def validate_observation_matrix(self) -> Self:
        provider_failures = {
            "provider_client_error",
            "provider_rate_limited",
            "provider_server_error",
            "provider_timeout",
            "provider_transport_error",
            "provider_response_too_large",
        }
        if self.status == "valid_shape":
            if (
                self.provider_response_receipt_id is None
                or self.response_validation_id is None
                or self.reason_code != "response_shape_valid"
            ):
                raise ValueError("valid shadow observation requires response and validation")
            if self.outer_diagnostic_id is not None:
                raise ValueError("valid shadow observation cannot carry an outer diagnostic")
        elif self.status == "invalid_output":
            artifacts_present = (
                self.provider_response_receipt_id is not None,
                self.response_validation_id is not None,
            )
            if artifacts_present == (False, False):
                if self.reason_code != "provider_outer_contract_invalid":
                    raise ValueError("outer-invalid observation uses an invalid reason")
                if self.outer_diagnostic_id is None:
                    raise ValueError("outer-invalid observation requires an outer diagnostic")
            elif artifacts_present == (True, True):
                if self.reason_code not in {
                    "evidence_out_of_range",
                    "incomplete_taxonomy",
                    "invalid_json",
                    "non_stop_finish_reason",
                    "response_empty",
                    "schema_invalid",
                }:
                    raise ValueError("inner-invalid observation uses an invalid reason")
                if self.outer_diagnostic_id is not None:
                    raise ValueError("inner-invalid observation cannot carry an outer diagnostic")
            else:
                raise ValueError("inner-invalid observation requires both response artifacts")
        elif self.status in provider_failures:
            if self.reason_code != self.status:
                raise ValueError("provider failure observation reason must equal status")
            if (
                self.provider_response_receipt_id is not None
                or self.response_validation_id is not None
                or self.outer_diagnostic_id is not None
            ):
                raise ValueError("provider failure cannot carry response artifacts")
        return self


class AITagShadowExecutionObservationV2(_AITagShadowExecutionObservationV2Payload):
    observation_id: Annotated[str, Field(pattern=_OBSERVATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-observation",
            identity_payload(self, "observation_id"),
        )
        if self.observation_id != expected:
            raise ValueError("shadow observation ID does not match its contents")
        return self


def seal_ai_tag_shadow_execution_observation_v2(
    payload: Mapping[str, object],
) -> AITagShadowExecutionObservationV2:
    return seal_payload(
        payload,
        payload_type=_AITagShadowExecutionObservationV2Payload,
        sealed_type=AITagShadowExecutionObservationV2,
        identity_field="observation_id",
        identity_prefix="ai-tag-shadow-observation",
        context="AI Tag Shadow Execution Observation V2",
    )


def load_ai_tag_shadow_execution_observation_v2(
    raw: str | bytes,
) -> AITagShadowExecutionObservationV2:
    return load_json_model(
        raw,
        AITagShadowExecutionObservationV2,
        "AI Tag Shadow Execution Observation V2",
    )


def verify_ai_tag_dispatch_attempt_receipt(
    receipt: AITagDispatchAttemptReceipt,
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
) -> None:
    receipt = AITagDispatchAttemptReceipt.model_validate(receipt.model_dump(mode="json"))
    plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
    claims = AITagShadowDispatchClaims.model_validate(claims.model_dump(mode="json"))
    if claims.plan_id != plan.plan_id:
        raise ValueError("shadow dispatch claims refer to a different plan")
    expected = (
        plan.plan_id,
        plan.envelope_id,
        plan.request_id,
        claims.claims_id,
        claims.trust_domain_id,
        claims.egress_approval_id,
        claims.budget_reservation_id,
        claims.credential_scope_id,
        plan.wire_body_sha256,
        plan.endpoint_url,
        plan.http_method,
        plan.tls_verify,
        plan.follow_redirects,
        plan.trust_env,
    )
    actual = (
        receipt.plan_id,
        receipt.envelope_id,
        receipt.request_id,
        receipt.claims_id,
        receipt.trust_domain_id,
        receipt.egress_approval_id,
        receipt.budget_reservation_id,
        receipt.credential_scope_id,
        receipt.wire_body_sha256,
        receipt.endpoint_url,
        receipt.http_method,
        receipt.tls_verify,
        receipt.follow_redirects,
        receipt.trust_env,
    )
    if actual != expected:
        raise ValueError("attempt receipt differs from its plan or dispatch claims")
    if (
        receipt.response_body_size_bytes is not None
        and receipt.response_body_size_bytes > plan.max_response_bytes
    ):
        raise ValueError("attempt response exceeds the frozen plan byte budget")


__all__ = [
    "AI_TAG_DISPATCH_ATTEMPT_RECEIPT_SCHEMA_VERSION",
    "AI_TAG_OBSERVED_RESPONSE_RECEIPT_SCHEMA_VERSION",
    "AI_TAG_OBSERVED_RESPONSE_RECEIPT_V2_SCHEMA_VERSION",
    "AI_TAG_SHADOW_DISPATCH_CLAIMS_SCHEMA_VERSION",
    "AI_TAG_SHADOW_DISPATCH_PLAN_SCHEMA_VERSION",
    "AI_TAG_SHADOW_PROVIDER_POLICY_SCHEMA_VERSION",
    "AI_TAG_SHADOW_EXECUTION_OBSERVATION_SCHEMA_VERSION",
    "AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION",
    "AITagDispatchAttemptReceipt",
    "AITagObservedProviderResponseReceipt",
    "AITagObservedProviderResponseReceiptV2",
    "AITagShadowDispatchClaims",
    "AITagShadowDispatchPlan",
    "AITagShadowProviderPolicy",
    "AITagShadowExecutionObservation",
    "AITagShadowExecutionObservationV2",
    "AttemptTransportStatus",
    "DeepSeekShadowChatPayload",
    "ShadowObservationStatus",
    "build_ai_tag_shadow_dispatch_plan",
    "load_ai_tag_dispatch_attempt_receipt",
    "load_ai_tag_observed_provider_response_receipt",
    "load_ai_tag_observed_provider_response_receipt_v2",
    "load_ai_tag_shadow_dispatch_claims",
    "load_ai_tag_shadow_dispatch_plan",
    "load_ai_tag_shadow_execution_observation",
    "load_ai_tag_shadow_execution_observation_v2",
    "seal_ai_tag_dispatch_attempt_receipt",
    "seal_ai_tag_observed_provider_response_receipt",
    "seal_ai_tag_observed_provider_response_receipt_v2",
    "seal_ai_tag_shadow_dispatch_claims",
    "seal_ai_tag_shadow_dispatch_plan",
    "seal_ai_tag_shadow_provider_policy",
    "seal_ai_tag_shadow_execution_observation",
    "seal_ai_tag_shadow_execution_observation_v2",
    "verify_ai_tag_dispatch_attempt_receipt",
    "verify_ai_tag_shadow_dispatch_plan",
]
