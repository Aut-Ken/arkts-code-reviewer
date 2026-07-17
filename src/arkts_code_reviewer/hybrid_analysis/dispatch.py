from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing import FeatureConfig
from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    ACTIVE_TAG_COUNT_V1,
    AITagAnalysisRequest,
    AITagContractView,
    AITagModelView,
    ReviewUnitAnalysisCard,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    AITagContractCatalog,
    AITagModelPolicy,
    AITagPromptAsset,
    FullTaxonomyRequestBuilder,
    verify_full_taxonomy_request,
)

AI_TAG_WIRE_USER_PAYLOAD_SCHEMA_VERSION: Literal["ai-tag-wire-user-payload-v1"] = (
    "ai-tag-wire-user-payload-v1"
)
AI_TAG_DISPATCH_ENVELOPE_SCHEMA_VERSION: Literal["ai-tag-dispatch-envelope-v1"] = (
    "ai-tag-dispatch-envelope-v1"
)
AI_TAG_DRY_RUN_RECEIPT_SCHEMA_VERSION: Literal["ai-tag-dry-run-receipt-v1"] = (
    "ai-tag-dry-run-receipt-v1"
)
AI_TAG_WIRE_RENDERER_VERSION: Literal["ai-tag-user-payload-renderer-v1"] = (
    "ai-tag-user-payload-renderer-v1"
)
AI_TAG_WIRE_OUTPUT_CONTRACT_VERSION: Literal["ai-tag-wire-output-v1"] = "ai-tag-wire-output-v1"
DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT: Literal["https://api.deepseek.com/chat/completions"] = (
    "https://api.deepseek.com/chat/completions"
)

_HASH = r"[0-9a-f]{64}"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_RECEIPT_ID = rf"^ai-tag-dry-run-receipt:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


class DeepSeekChatMessage(FrozenModel):
    role: Literal["system", "user"]
    content: Annotated[str, Field(min_length=1, max_length=2_000_000)]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if "\x00" in value or value.startswith("\ufeff"):
            raise ValueError("DeepSeekChatMessage.content contains a forbidden marker")
        return value


class DeepSeekThinking(FrozenModel):
    type: Literal["disabled"]


class DeepSeekResponseFormat(FrozenModel):
    type: Literal["json_object"]


class DeepSeekChatPayload(FrozenModel):
    """The complete no-secret JSON body rendered for DeepSeek Chat Completions."""

    model: Literal["deepseek-v4-pro"]
    messages: Annotated[tuple[DeepSeekChatMessage, ...], Field(min_length=2, max_length=2)]
    thinking: DeepSeekThinking
    temperature: int
    stream: Literal[False]
    tool_choice: Literal["none"]
    response_format: DeepSeekResponseFormat

    @field_validator("messages", mode="before")
    @classmethod
    def parse_messages(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DeepSeekChatPayload.messages")

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature(cls, value: object) -> object:
        if type(value) is not int or value != 0:
            raise ValueError("DeepSeekChatPayload.temperature must be the integer 0")
        return value

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        if tuple(message.role for message in self.messages) != ("system", "user"):
            raise ValueError("DeepSeekChatPayload messages must be system then user")
        return self


class AITagWireUserPayload(FrozenModel):
    schema_version: Literal["ai-tag-wire-user-payload-v1"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    required_tag_count: Literal[24]
    model_view: AITagModelView
    tag_contract_views: Annotated[
        tuple[AITagContractView, ...],
        Field(min_length=24, max_length=24),
    ]

    @field_validator("tag_contract_views", mode="before")
    @classmethod
    def parse_contracts(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagWireUserPayload.tag_contract_views")

    @model_validator(mode="after")
    def validate_taxonomy(self) -> Self:
        tag_ids = tuple(contract.tag_id for contract in self.tag_contract_views)
        if tag_ids != tuple(sorted(set(tag_ids))) or len(tag_ids) != ACTIVE_TAG_COUNT_V1:
            raise ValueError(
                "AITagWireUserPayload contracts must use canonical unique 24-Tag order"
            )
        return self


class _VerifiedAITagDispatchEnvelopePayload(FrozenModel):
    schema_version: Literal["ai-tag-dispatch-envelope-v1"]
    analysis_request: AITagAnalysisRequest
    model_view: AITagModelView
    prompt: AITagPromptAsset
    model_policy: AITagModelPolicy
    user_payload_renderer_version: Literal["ai-tag-user-payload-renderer-v1"]
    wire_output_contract_version: Literal["ai-tag-wire-output-v1"]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    user_payload: AITagWireUserPayload
    wire_payload: DeepSeekChatPayload
    wire_body_json: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    dispatch_authorization: Literal["not_authorized_no_budget_no_approval"]

    @model_validator(mode="after")
    def validate_bound_graph(self) -> Self:
        request = self.analysis_request
        if request.model_view_id != self.model_view.model_view_id:
            raise ValueError("dispatch envelope Request and Model View differ")
        if request.card_id != self.model_view.card_id:
            raise ValueError("dispatch envelope Request and Model View Card differ")
        if (
            self.user_payload.request_id != request.request_id
            or self.user_payload.required_tag_count != request.required_tag_count
            or self.user_payload.model_view != self.model_view
            or self.user_payload.tag_contract_views != request.tag_contract_views
        ):
            raise ValueError("dispatch envelope user payload differs from Request inputs")
        if (
            request.prompt_version != self.prompt.prompt_version
            or request.prompt_hash != self.prompt.prompt_hash
        ):
            raise ValueError("dispatch envelope Prompt differs from Request")
        if request.model_policy_fingerprint != self.model_policy.model_policy_fingerprint:
            raise ValueError("dispatch envelope Model Policy differs from Request")
        if (
            self.model_policy.user_payload_renderer_version != self.user_payload_renderer_version
            or self.model_policy.wire_output_contract_version != self.wire_output_contract_version
        ):
            raise ValueError("dispatch envelope renderer versions differ from Model Policy")
        expected_messages = (
            DeepSeekChatMessage(role="system", content=self.prompt.text),
            DeepSeekChatMessage(
                role="user",
                content=canonical_json(self.user_payload.model_dump(mode="json")),
            ),
        )
        expected_wire = DeepSeekChatPayload(
            model=self.model_policy.model,
            messages=expected_messages,
            thinking=DeepSeekThinking(type=self.model_policy.thinking),
            temperature=self.model_policy.temperature,
            stream=self.model_policy.stream,
            tool_choice=self.model_policy.tool_choice,
            response_format=DeepSeekResponseFormat(
                type=self.model_policy.response_format,
            ),
        )
        if self.wire_payload != expected_wire:
            raise ValueError("dispatch envelope wire payload differs from bound inputs")
        expected_body = canonical_json(expected_wire.model_dump(mode="json"))
        if self.wire_body_json != expected_body:
            raise ValueError("dispatch envelope wire JSON is not canonical or bound")
        expected_hash = f"sha256:{hashlib.sha256(expected_body.encode('utf-8')).hexdigest()}"
        if self.wire_body_sha256 != expected_hash:
            raise ValueError("dispatch envelope wire body hash does not match")
        return self


class VerifiedAITagDispatchEnvelope(_VerifiedAITagDispatchEnvelopePayload):
    """Content-addressed render artifact; provenance still requires trusted rebuild."""

    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]

    @model_validator(mode="after")
    def validate_envelope_id(self) -> Self:
        expected = canonical_hash(
            "ai-tag-dispatch-envelope",
            identity_payload(self, "envelope_id"),
        )
        if self.envelope_id != expected:
            raise ValueError("dispatch envelope ID does not match its complete contents")
        return self


def seal_verified_ai_tag_dispatch_envelope(
    payload: Mapping[str, object],
) -> VerifiedAITagDispatchEnvelope:
    return seal_payload(
        payload,
        payload_type=_VerifiedAITagDispatchEnvelopePayload,
        sealed_type=VerifiedAITagDispatchEnvelope,
        identity_field="envelope_id",
        identity_prefix="ai-tag-dispatch-envelope",
        context="Verified AI Tag Dispatch Envelope",
    )


def load_verified_ai_tag_dispatch_envelope(
    raw: str | bytes,
) -> VerifiedAITagDispatchEnvelope:
    return load_json_model(
        raw,
        VerifiedAITagDispatchEnvelope,
        "Verified AI Tag Dispatch Envelope",
    )


@dataclass(frozen=True)
class AITagDispatchEnvelopeBuilder:
    request_builder: FullTaxonomyRequestBuilder

    @classmethod
    def default(cls) -> AITagDispatchEnvelopeBuilder:
        return cls(request_builder=FullTaxonomyRequestBuilder.default())

    def build(
        self,
        *,
        card: ReviewUnitAnalysisCard,
        model_view: AITagModelView,
        request: AITagAnalysisRequest,
    ) -> VerifiedAITagDispatchEnvelope:
        trusted = self.request_builder
        verify_full_taxonomy_request(
            request,
            card=card,
            model_view=model_view,
            feature_config=trusted.feature_config,
            catalog=trusted.catalog,
            prompt=trusted.prompt,
            model_policy=trusted.model_policy,
        )
        policy = trusted.model_policy
        if policy.user_payload_renderer_version != AI_TAG_WIRE_RENDERER_VERSION:
            raise ValueError("AI Tag Model Policy does not authorize this renderer version")
        if policy.wire_output_contract_version != AI_TAG_WIRE_OUTPUT_CONTRACT_VERSION:
            raise ValueError("AI Tag Model Policy does not authorize this wire contract")
        user_payload = AITagWireUserPayload(
            schema_version=AI_TAG_WIRE_USER_PAYLOAD_SCHEMA_VERSION,
            request_id=request.request_id,
            required_tag_count=request.required_tag_count,
            model_view=model_view,
            tag_contract_views=request.tag_contract_views,
        )
        wire_payload = DeepSeekChatPayload(
            model=policy.model,
            messages=(
                DeepSeekChatMessage(role="system", content=trusted.prompt.text),
                DeepSeekChatMessage(
                    role="user",
                    content=canonical_json(user_payload.model_dump(mode="json")),
                ),
            ),
            thinking=DeepSeekThinking(type=policy.thinking),
            temperature=policy.temperature,
            stream=policy.stream,
            tool_choice=policy.tool_choice,
            response_format=DeepSeekResponseFormat(type=policy.response_format),
        )
        wire_body_json = canonical_json(wire_payload.model_dump(mode="json"))
        return seal_verified_ai_tag_dispatch_envelope(
            {
                "schema_version": AI_TAG_DISPATCH_ENVELOPE_SCHEMA_VERSION,
                "analysis_request": request,
                "model_view": model_view,
                "prompt": trusted.prompt,
                "model_policy": policy,
                "user_payload_renderer_version": AI_TAG_WIRE_RENDERER_VERSION,
                "wire_output_contract_version": AI_TAG_WIRE_OUTPUT_CONTRACT_VERSION,
                "endpoint_url": DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
                "user_payload": user_payload,
                "wire_payload": wire_payload,
                "wire_body_json": wire_body_json,
                "wire_body_sha256": (
                    "sha256:" + hashlib.sha256(wire_body_json.encode("utf-8")).hexdigest()
                ),
                "dispatch_authorization": "not_authorized_no_budget_no_approval",
            }
        )


def build_verified_ai_tag_dispatch_envelope(
    *,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    request: AITagAnalysisRequest,
) -> VerifiedAITagDispatchEnvelope:
    return AITagDispatchEnvelopeBuilder.default().build(
        card=card,
        model_view=model_view,
        request=request,
    )


def verify_ai_tag_dispatch_envelope(
    envelope: VerifiedAITagDispatchEnvelope,
    *,
    card: ReviewUnitAnalysisCard,
    model_view: AITagModelView,
    request: AITagAnalysisRequest,
    feature_config: FeatureConfig,
    catalog: AITagContractCatalog,
    prompt: AITagPromptAsset,
    model_policy: AITagModelPolicy,
) -> None:
    try:
        envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    except ValidationError as exc:
        raise ValueError(f"invalid Verified AI Tag Dispatch Envelope: {exc}") from exc
    expected = AITagDispatchEnvelopeBuilder(
        request_builder=FullTaxonomyRequestBuilder(
            feature_config=feature_config,
            catalog=catalog,
            prompt=prompt,
            model_policy=model_policy,
        )
    ).build(card=card, model_view=model_view, request=request)
    if envelope != expected:
        raise ValueError("dispatch envelope differs from trusted-input deterministic rebuild")


class _AITagDryRunReceiptPayload(FrozenModel):
    schema_version: Literal["ai-tag-dry-run-receipt-v1"]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    wire_body_size_bytes: Annotated[int, Field(ge=1, le=8_000_000)]
    network_attempted: Literal[False]
    status: Literal["rendered_not_dispatched"]


class AITagDryRunReceipt(_AITagDryRunReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_receipt_id(self) -> Self:
        expected = canonical_hash(
            "ai-tag-dry-run-receipt",
            identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("dry-run receipt ID does not match its complete contents")
        return self


def seal_ai_tag_dry_run_receipt(payload: Mapping[str, object]) -> AITagDryRunReceipt:
    return seal_payload(
        payload,
        payload_type=_AITagDryRunReceiptPayload,
        sealed_type=AITagDryRunReceipt,
        identity_field="receipt_id",
        identity_prefix="ai-tag-dry-run-receipt",
        context="AI Tag Dry Run Receipt",
    )


def load_ai_tag_dry_run_receipt(raw: str | bytes) -> AITagDryRunReceipt:
    return load_json_model(raw, AITagDryRunReceipt, "AI Tag Dry Run Receipt")


def verify_ai_tag_dry_run_receipt(
    receipt: AITagDryRunReceipt,
    envelope: VerifiedAITagDispatchEnvelope,
) -> None:
    receipt = AITagDryRunReceipt.model_validate(receipt.model_dump(mode="json"))
    envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    if (
        receipt.envelope_id != envelope.envelope_id
        or receipt.request_id != envelope.analysis_request.request_id
        or receipt.endpoint_url != envelope.endpoint_url
        or receipt.wire_body_sha256 != envelope.wire_body_sha256
        or receipt.wire_body_size_bytes != len(envelope.wire_body_json.encode("utf-8"))
    ):
        raise ValueError("AI Tag dry-run receipt does not reference its envelope")


class DryRunTagAnalysisClient:
    """Render-only client. It cannot produce a model Result or ExecutionOutcome."""

    def preview(self, envelope: VerifiedAITagDispatchEnvelope) -> AITagDryRunReceipt:
        try:
            envelope = VerifiedAITagDispatchEnvelope.model_validate(
                envelope.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise ValueError(f"invalid dispatch envelope: {exc}") from exc
        return seal_ai_tag_dry_run_receipt(
            {
                "schema_version": AI_TAG_DRY_RUN_RECEIPT_SCHEMA_VERSION,
                "envelope_id": envelope.envelope_id,
                "request_id": envelope.analysis_request.request_id,
                "endpoint_url": envelope.endpoint_url,
                "wire_body_sha256": envelope.wire_body_sha256,
                "wire_body_size_bytes": len(envelope.wire_body_json.encode("utf-8")),
                "network_attempted": False,
                "status": "rendered_not_dispatched",
            }
        )


class AITagProviderTransport(Protocol):
    def send(self, envelope: VerifiedAITagDispatchEnvelope) -> object: ...


class ProviderDispatchDisabledError(RuntimeError):
    pass


def dispatch_ai_tag_envelope(
    envelope: VerifiedAITagDispatchEnvelope,
    transport: AITagProviderTransport,
) -> object:
    """Unconditionally fail before transport access for the render-only v1 contract."""

    try:
        envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    except ValidationError as exc:
        raise ValueError(f"invalid dispatch envelope: {exc}") from exc
    del transport
    raise ProviderDispatchDisabledError(
        "DeepSeek dispatch is disabled: v1 has no budget, compliance, or attempt receipt"
    )


__all__ = [
    "AI_TAG_DISPATCH_ENVELOPE_SCHEMA_VERSION",
    "AI_TAG_DRY_RUN_RECEIPT_SCHEMA_VERSION",
    "AI_TAG_WIRE_OUTPUT_CONTRACT_VERSION",
    "AI_TAG_WIRE_RENDERER_VERSION",
    "AI_TAG_WIRE_USER_PAYLOAD_SCHEMA_VERSION",
    "DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT",
    "AITagDispatchEnvelopeBuilder",
    "AITagDryRunReceipt",
    "AITagProviderTransport",
    "AITagWireUserPayload",
    "DeepSeekChatMessage",
    "DeepSeekChatPayload",
    "DeepSeekResponseFormat",
    "DeepSeekThinking",
    "DryRunTagAnalysisClient",
    "ProviderDispatchDisabledError",
    "VerifiedAITagDispatchEnvelope",
    "build_verified_ai_tag_dispatch_envelope",
    "dispatch_ai_tag_envelope",
    "load_ai_tag_dry_run_receipt",
    "load_verified_ai_tag_dispatch_envelope",
    "seal_ai_tag_dry_run_receipt",
    "seal_verified_ai_tag_dispatch_envelope",
    "verify_ai_tag_dispatch_envelope",
    "verify_ai_tag_dry_run_receipt",
]
