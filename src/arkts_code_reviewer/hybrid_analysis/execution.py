from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    identity_payload,
    load_json_model,
    load_json_object,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import VerifiedAITagDispatchEnvelope
from arkts_code_reviewer.hybrid_analysis.models import (
    ACTIVE_TAG_COUNT_V1,
    AITagJudgment,
    AITagUsage,
)

AI_TAG_RESPONSE_VALIDATION_SCHEMA_VERSION = "ai-tag-response-validation-v1"

InvalidOutputReason = Literal[
    "evidence_out_of_range",
    "incomplete_taxonomy",
    "invalid_json",
    "non_stop_finish_reason",
    "response_empty",
    "schema_invalid",
]
ProviderFailureReason = Literal[
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
]
RawCompletionSource = Literal["scripted_fixture", "unverified_raw"]
TransportFailureSource = Literal[
    "scripted_fixture",
    "unverified_transport_claim",
]
ResponseValidationStatus = Literal[
    "valid_shape",
    "invalid_output",
    "unavailable_claim",
]
ResponseValidationReason = Literal[
    "response_shape_valid",
    "evidence_out_of_range",
    "incomplete_taxonomy",
    "invalid_json",
    "non_stop_finish_reason",
    "response_empty",
    "schema_invalid",
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
]

_HASH = r"[0-9a-f]{64}"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_VALIDATION_ID = rf"^ai-tag-response-validation:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"

_INVALID_REASONS: set[str] = {
    "evidence_out_of_range",
    "incomplete_taxonomy",
    "invalid_json",
    "non_stop_finish_reason",
    "response_empty",
    "schema_invalid",
}
_UNAVAILABLE_REASONS: set[str] = {
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
}


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _bounded_single_line(value: str, context: str, max_length: int = 500) -> str:
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


class AITagWireOutput(FrozenModel):
    """The only accepted JSON object inside an assistant message."""

    judgments: tuple[AITagJudgment, ...]

    @field_validator("judgments", mode="before")
    @classmethod
    def parse_judgments(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagWireOutput.judgments")


class AITagRawUsage(FrozenModel):
    """Usage claim before the all-reported-or-all-null normalization."""

    prompt_tokens: Annotated[int | None, Field(ge=0)] = None
    completion_tokens: Annotated[int | None, Field(ge=0)] = None
    prompt_cache_hit_tokens: Annotated[int | None, Field(ge=0)] = None


class AITagRawCompletion(FrozenModel):
    """Unattributed completion input. It can only produce a validation artifact."""

    source_kind: RawCompletionSource
    content: Annotated[str | None, Field(max_length=2_000_000)]
    finish_reason: Annotated[str, Field(min_length=1, max_length=100)]
    model: Annotated[str, Field(min_length=1, max_length=100)]
    system_fingerprint: Annotated[str | None, Field(max_length=500)] = None
    usage: AITagRawUsage | None = None
    latency_ms: Annotated[int, Field(ge=0)]
    attempt_count: Annotated[int, Field(ge=1)]

    @field_validator("finish_reason", "model")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return _bounded_single_line(value, f"AITagRawCompletion.{info.field_name}", 100)

    @field_validator("system_fingerprint")
    @classmethod
    def validate_system_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_single_line(value, "AITagRawCompletion.system_fingerprint")


class AITagTransportFailure(FrozenModel):
    """Unverified failure claim; no raw provider body is retained."""

    source_kind: TransportFailureSource
    reason_code: ProviderFailureReason
    attempt_count: Annotated[int, Field(ge=1)]
    latency_ms: Annotated[int, Field(ge=0)]


class _AITagResponseValidationPayload(FrozenModel):
    schema_version: Literal["ai-tag-response-validation-v1"]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    raw_content_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    source_kind: Literal[
        "scripted_fixture",
        "unverified_raw",
        "unverified_transport_claim",
    ]
    status: ResponseValidationStatus
    reason_code: ResponseValidationReason
    model: Annotated[str | None, Field(max_length=100)]
    system_fingerprint: Annotated[str | None, Field(max_length=500)]
    finish_reason: Annotated[str | None, Field(max_length=100)]
    judgments: tuple[AITagJudgment, ...]
    usage: AITagUsage
    latency_ms: Annotated[int, Field(ge=0)]
    attempt_count: Annotated[int, Field(ge=1)]
    qualification: Literal["synthetic_or_unattributed_not_formal"]

    @field_validator("judgments", mode="before")
    @classmethod
    def parse_judgments(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "AITagResponseValidation.judgments")

    @field_validator("model", "system_fingerprint", "finish_reason")
    @classmethod
    def validate_optional_text(cls, value: str | None, info) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        maximum = 500 if info.field_name == "system_fingerprint" else 100
        return _bounded_single_line(
            value,
            f"AITagResponseValidation.{info.field_name}",
            maximum,
        )

    @model_validator(mode="after")
    def validate_status_matrix(self) -> Self:
        tag_ids = tuple(judgment.tag_id for judgment in self.judgments)
        if any(
            judgment.reason is not None and len(judgment.reason) > 500
            for judgment in self.judgments
        ):
            raise ValueError("response validation judgment reason exceeds 500 characters")
        if self.status == "valid_shape":
            if self.reason_code != "response_shape_valid":
                raise ValueError("valid response validation uses an invalid reason")
            if len(tag_ids) != ACTIVE_TAG_COUNT_V1 or tag_ids != tuple(sorted(set(tag_ids))):
                raise ValueError(
                    "valid response validation requires canonical unique 24-Tag judgments"
                )
            if self.model is None or self.finish_reason != "stop":
                raise ValueError("valid response validation requires model and stop finish")
            if self.raw_content_sha256 is None:
                raise ValueError("valid response validation requires raw content identity")
            if self.source_kind == "unverified_transport_claim":
                raise ValueError("transport failure source cannot carry valid judgments")
        elif self.status == "invalid_output":
            if self.reason_code not in _INVALID_REASONS or self.judgments:
                raise ValueError("invalid response validation status matrix is inconsistent")
            if self.model is None or self.finish_reason is None:
                raise ValueError("invalid response validation requires completion metadata")
            if self.raw_content_sha256 is None:
                raise ValueError("invalid response validation requires raw content identity")
            if self.source_kind == "unverified_transport_claim":
                raise ValueError("transport failure source cannot carry invalid output")
        else:
            if self.reason_code not in _UNAVAILABLE_REASONS or self.judgments:
                raise ValueError("unavailable response validation status matrix is inconsistent")
            if any(
                value is not None
                for value in (self.model, self.system_fingerprint, self.finish_reason)
            ):
                raise ValueError("unavailable claim cannot carry completion metadata")
            if self.raw_content_sha256 is not None:
                raise ValueError("unavailable claim cannot carry raw content identity")
            if self.source_kind == "unverified_raw":
                raise ValueError("raw completion source cannot carry unavailable claim")
        return self


class AITagResponseValidation(_AITagResponseValidationPayload):
    """Diagnostic-only response validation; never a formal provider Result."""

    validation_id: Annotated[str, Field(pattern=_VALIDATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-response-validation",
            identity_payload(self, "validation_id"),
        )
        if self.validation_id != expected:
            raise ValueError("AI Tag response validation ID does not match its contents")
        return self


def seal_ai_tag_response_validation(
    payload: Mapping[str, object],
) -> AITagResponseValidation:
    return seal_payload(
        payload,
        payload_type=_AITagResponseValidationPayload,
        sealed_type=AITagResponseValidation,
        identity_field="validation_id",
        identity_prefix="ai-tag-response-validation",
        context="AI Tag Response Validation",
    )


def load_ai_tag_response_validation(raw: str | bytes) -> AITagResponseValidation:
    return load_json_model(raw, AITagResponseValidation, "AI Tag Response Validation")


class AITagResponseValidationError(ValueError):
    def __init__(self, reason_code: InvalidOutputReason, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _is_degraded(envelope: VerifiedAITagDispatchEnvelope) -> bool:
    model_view = envelope.model_view
    return (
        model_view.code.truncated
        or model_view.quality.parser_layer != "L1"
        or model_view.quality.context_degraded
        or model_view.quality.unit_owner_unresolved
        or model_view.owner_summary.resolution in {"partial", "unresolved"}
        or bool(model_view.quality.error_nodes)
        or bool(model_view.quality.missing_nodes)
    )


def _validate_wire_output(
    content: str,
    envelope: VerifiedAITagDispatchEnvelope,
) -> tuple[AITagJudgment, ...]:
    try:
        payload = load_json_object(content, "AI Tag raw response")
    except (TypeError, ValueError) as exc:
        raise AITagResponseValidationError(
            "invalid_json",
            "AI Tag raw response is not one strict JSON object",
        ) from exc
    if set(payload) != {"judgments"}:
        raise AITagResponseValidationError(
            "schema_invalid",
            "AI Tag raw response has unexpected top-level fields",
        )
    raw_judgments = payload.get("judgments")
    if not isinstance(raw_judgments, list):
        raise AITagResponseValidationError(
            "schema_invalid",
            "AI Tag raw judgments must be an array",
        )
    expected_tag_ids = tuple(
        contract.tag_id for contract in envelope.analysis_request.tag_contract_views
    )
    raw_tag_ids = tuple(
        item.get("tag_id") if isinstance(item, dict) else None for item in raw_judgments
    )
    if len(raw_judgments) != ACTIVE_TAG_COUNT_V1 or raw_tag_ids != expected_tag_ids:
        raise AITagResponseValidationError(
            "incomplete_taxonomy",
            "AI Tag raw response does not preserve the complete requested taxonomy",
        )
    try:
        wire_output = AITagWireOutput.model_validate(payload)
    except ValidationError as exc:
        raise AITagResponseValidationError(
            "schema_invalid",
            "AI Tag raw judgments violate the closed output schema",
        ) from exc
    if any(
        judgment.reason is not None and len(judgment.reason) > 500
        for judgment in wire_output.judgments
    ):
        raise AITagResponseValidationError(
            "schema_invalid",
            "AI Tag raw judgment reason exceeds the diagnostic bound",
        )
    visible_lines = set(envelope.model_view.code.line_numbers)
    if any(
        not set(judgment.evidence_lines).issubset(visible_lines)
        for judgment in wire_output.judgments
    ):
        raise AITagResponseValidationError(
            "evidence_out_of_range",
            "AI Tag raw response cites a line outside the Model View",
        )
    if _is_degraded(envelope) and any(
        judgment.decision == "not_supported" for judgment in wire_output.judgments
    ):
        raise AITagResponseValidationError(
            "schema_invalid",
            "degraded Model View cannot support not_supported judgments",
        )
    return wire_output.judgments


def _normalize_usage(raw: AITagRawUsage | None) -> AITagUsage:
    if raw is None:
        values: tuple[int | None, int | None, int | None] = (None, None, None)
    else:
        values = (
            raw.prompt_tokens,
            raw.completion_tokens,
            raw.prompt_cache_hit_tokens,
        )
        if any(value is None for value in values):
            values = (None, None, None)
    return AITagUsage(
        input_tokens=values[0],
        output_tokens=values[1],
        cache_read_input_tokens=values[2],
    )


def _completion_validation_payload(
    *,
    envelope: VerifiedAITagDispatchEnvelope,
    completion: AITagRawCompletion,
    status: Literal["valid_shape", "invalid_output"],
    reason_code: ResponseValidationReason,
    judgments: tuple[AITagJudgment, ...],
) -> dict[str, object]:
    raw_identity_bytes = (
        b"<null-content>" if completion.content is None else completion.content.encode("utf-8")
    )
    return {
        "schema_version": AI_TAG_RESPONSE_VALIDATION_SCHEMA_VERSION,
        "envelope_id": envelope.envelope_id,
        "request_id": envelope.analysis_request.request_id,
        "wire_body_sha256": envelope.wire_body_sha256,
        "raw_content_sha256": ("sha256:" + hashlib.sha256(raw_identity_bytes).hexdigest()),
        "source_kind": completion.source_kind,
        "status": status,
        "reason_code": reason_code,
        "model": completion.model,
        "system_fingerprint": completion.system_fingerprint or "not_reported",
        "finish_reason": completion.finish_reason,
        "judgments": judgments,
        "usage": _normalize_usage(completion.usage),
        "latency_ms": completion.latency_ms,
        "attempt_count": completion.attempt_count,
        "qualification": "synthetic_or_unattributed_not_formal",
    }


def validate_ai_tag_completion(
    envelope: VerifiedAITagDispatchEnvelope,
    completion: AITagRawCompletion,
) -> AITagResponseValidation:
    """Validate raw content without claiming that DeepSeek produced it.

    This function can never construct AITagAnalysisResult or
    AITagExecutionOutcome. A future trusted transport slice must provide its own
    attempt and provider-response receipts before any formal conversion exists.
    """

    envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    completion = AITagRawCompletion.model_validate(completion.model_dump(mode="json"))
    try:
        if completion.content is None or not completion.content.strip():
            raise AITagResponseValidationError(
                "response_empty",
                "AI Tag raw response content is empty",
            )
        if completion.finish_reason != "stop":
            raise AITagResponseValidationError(
                "non_stop_finish_reason",
                "AI Tag raw response did not finish with stop",
            )
        if completion.model != envelope.model_policy.model:
            raise AITagResponseValidationError(
                "schema_invalid",
                "AI Tag raw response model differs from the frozen policy",
            )
        judgments = _validate_wire_output(completion.content, envelope)
    except AITagResponseValidationError as exc:
        return seal_ai_tag_response_validation(
            _completion_validation_payload(
                envelope=envelope,
                completion=completion,
                status="invalid_output",
                reason_code=exc.reason_code,
                judgments=(),
            )
        )
    return seal_ai_tag_response_validation(
        _completion_validation_payload(
            envelope=envelope,
            completion=completion,
            status="valid_shape",
            reason_code="response_shape_valid",
            judgments=judgments,
        )
    )


def validate_ai_tag_transport_failure(
    envelope: VerifiedAITagDispatchEnvelope,
    failure: AITagTransportFailure,
) -> AITagResponseValidation:
    """Validate a failure claim without claiming that a network attempt occurred."""

    envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    failure = AITagTransportFailure.model_validate(failure.model_dump(mode="json"))
    return seal_ai_tag_response_validation(
        {
            "schema_version": AI_TAG_RESPONSE_VALIDATION_SCHEMA_VERSION,
            "envelope_id": envelope.envelope_id,
            "request_id": envelope.analysis_request.request_id,
            "wire_body_sha256": envelope.wire_body_sha256,
            "raw_content_sha256": None,
            "source_kind": failure.source_kind,
            "status": "unavailable_claim",
            "reason_code": failure.reason_code,
            "model": None,
            "system_fingerprint": None,
            "finish_reason": None,
            "judgments": (),
            "usage": AITagUsage(
                input_tokens=None,
                output_tokens=None,
                cache_read_input_tokens=None,
            ),
            "latency_ms": failure.latency_ms,
            "attempt_count": failure.attempt_count,
            "qualification": "synthetic_or_unattributed_not_formal",
        }
    )


def verify_ai_tag_response_validation(
    validation: AITagResponseValidation,
    envelope: VerifiedAITagDispatchEnvelope,
) -> None:
    validation = AITagResponseValidation.model_validate(validation.model_dump(mode="json"))
    envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    if (
        validation.envelope_id != envelope.envelope_id
        or validation.request_id != envelope.analysis_request.request_id
        or validation.wire_body_sha256 != envelope.wire_body_sha256
    ):
        raise ValueError("AI Tag response validation does not reference its envelope")
    if validation.status == "valid_shape":
        expected_tag_ids = tuple(
            contract.tag_id for contract in envelope.analysis_request.tag_contract_views
        )
        if tuple(item.tag_id for item in validation.judgments) != expected_tag_ids:
            raise ValueError("AI Tag response validation taxonomy differs from envelope")
        visible_lines = set(envelope.model_view.code.line_numbers)
        if any(
            not set(item.evidence_lines).issubset(visible_lines) for item in validation.judgments
        ):
            raise ValueError("AI Tag response validation cites an invisible line")
        if _is_degraded(envelope) and any(
            item.decision == "not_supported" for item in validation.judgments
        ):
            raise ValueError("degraded envelope cannot verify not_supported validation judgments")


__all__ = [
    "AI_TAG_RESPONSE_VALIDATION_SCHEMA_VERSION",
    "AITagRawCompletion",
    "AITagRawUsage",
    "AITagResponseValidation",
    "AITagResponseValidationError",
    "AITagTransportFailure",
    "AITagWireOutput",
    "InvalidOutputReason",
    "ProviderFailureReason",
    "RawCompletionSource",
    "ResponseValidationReason",
    "ResponseValidationStatus",
    "TransportFailureSource",
    "load_ai_tag_response_validation",
    "seal_ai_tag_response_validation",
    "validate_ai_tag_completion",
    "validate_ai_tag_transport_failure",
    "verify_ai_tag_response_validation",
]
