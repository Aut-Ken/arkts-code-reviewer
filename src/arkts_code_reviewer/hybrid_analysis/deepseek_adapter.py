from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, NoReturn, Protocol, Self, cast

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagRawCompletion,
    AITagRawUsage,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AI_TAG_OBSERVED_RESPONSE_RECEIPT_V2_SCHEMA_VERSION,
    AITagDispatchAttemptReceipt,
    AITagObservedProviderResponseReceiptV2,
    AITagShadowDispatchPlan,
    seal_ai_tag_observed_provider_response_receipt_v2,
)

if TYPE_CHECKING:
    import httpx


class DeepSeekCredentialUnavailableError(RuntimeError):
    pass


class DeepSeekCredentialProvider(Protocol):
    @property
    def credential_scope_id(self) -> str: ...

    def is_configured(self) -> bool: ...

    def get_api_key(self) -> str: ...


class _DeepSeekCredentialSettings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=None,
        env_prefix="",
        extra="ignore",
        frozen=True,
    )

    api_key: SecretStr = Field(validation_alias="DEEPSEEK_API_KEY")


class EnvironmentDeepSeekCredentialProvider:
    """Read the only supported credential lazily and never serialize it."""

    _ENVIRONMENT_VARIABLE = "DEEPSEEK_API_KEY"

    @property
    def credential_scope_id(self) -> str:
        return canonical_hash(
            "deepseek-credential-scope",
            {"source": "environment", "name": self._ENVIRONMENT_VARIABLE},
        )

    def is_configured(self) -> bool:
        try:
            self.get_api_key()
        except DeepSeekCredentialUnavailableError:
            return False
        return True

    def get_api_key(self) -> str:
        try:
            value = _DeepSeekCredentialSettings().api_key.get_secret_value()  # type: ignore[call-arg]
        except ValidationError:
            raise DeepSeekCredentialUnavailableError(
                "DeepSeek API credential is not configured"
            ) from None
        if (
            not value
            or value != value.strip()
            or len(value) > 4_096
            or any(ord(character) < 33 or ord(character) == 127 for character in value)
        ):
            raise DeepSeekCredentialUnavailableError("DeepSeek API credential is invalid")
        return value


class DeepSeekHttpTransportError(RuntimeError):
    def __init__(
        self,
        kind: Literal[
            "provider_timeout",
            "provider_transport_error",
            "provider_response_too_large",
        ],
        *,
        latency_ms: int,
    ) -> None:
        super().__init__(kind)
        if type(latency_ms) is not int or latency_ms < 0:
            raise ValueError("DeepSeek transport failure latency must be a non-negative integer")
        self.kind = kind
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class DeepSeekHttpResponse:
    status_code: int
    body: bytes
    retry_after_ms: int | None
    latency_ms: int

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("DeepSeek HTTP status is outside the valid range")
        if not isinstance(self.body, bytes):
            raise ValueError("DeepSeek HTTP response body must use bytes")
        if type(self.latency_ms) is not int or self.latency_ms < 0:
            raise ValueError("DeepSeek HTTP latency cannot be negative")
        if self.retry_after_ms is not None and (
            type(self.retry_after_ms) is not int or not 0 <= self.retry_after_ms <= 120_000
        ):
            raise ValueError("DeepSeek Retry-After is outside the retained range")
        if self.retry_after_ms is not None and self.status_code != 429:
            raise ValueError("DeepSeek Retry-After is only meaningful for HTTP 429")


class DeepSeekShadowHttpTransport(Protocol):
    """Test-injectable transport; the runner supplies only a synthetic token."""

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse: ...


class _HttpxDeepSeekShadowTransport:
    """Optional real transport. It performs no retry and retains no secret or body."""

    def __init__(self, *, http_transport: object | None = None) -> None:
        self._http_transport = http_transport

    @property
    def establishes_fixed_tls_network_evidence(self) -> bool:
        return self._http_transport is None

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        if not api_key or api_key != api_key.strip():
            raise DeepSeekCredentialUnavailableError("DeepSeek API credential is invalid")
        started = time.monotonic_ns()
        try:
            import httpx

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "synchronous DeepSeek transport cannot run inside an active event loop"
                )
            return asyncio.run(
                self._send_with_absolute_deadline(
                    plan=plan,
                    api_key=api_key,
                    started_ns=started,
                )
            )
        except DeepSeekHttpTransportError:
            raise
        except Exception as exc:
            try:
                import httpx
            except ImportError:
                raise RuntimeError(
                    "DeepSeek transport requires the optional 'deepseek' dependency"
                ) from None
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                kind: Literal["provider_timeout", "provider_transport_error"] = "provider_timeout"
            elif isinstance(exc, (httpx.HTTPError, OSError)):
                kind = "provider_transport_error"
            elif isinstance(exc, ImportError):
                raise RuntimeError(
                    "DeepSeek transport requires the optional 'deepseek' dependency"
                ) from None
            else:
                kind = "provider_transport_error"
            raise DeepSeekHttpTransportError(kind, latency_ms=_elapsed_ms(started)) from None

    async def _send_with_absolute_deadline(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        api_key: str,
        started_ns: int,
    ) -> DeepSeekHttpResponse:
        import httpx

        transport = cast("httpx.AsyncBaseTransport | None", self._http_transport)
        deadline_ns = started_ns + plan.wall_clock_timeout_ms * 1_000_000
        remaining_seconds = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
        if remaining_seconds <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining_seconds):
            async with httpx.AsyncClient(
                verify=plan.tls_verify,
                follow_redirects=plan.follow_redirects,
                trust_env=plan.trust_env,
                timeout=httpx.Timeout(remaining_seconds),
                transport=transport,
            ) as client:
                async with client.stream(
                    plan.http_method,
                    plan.endpoint_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    content=plan.wire_body_json.encode("utf-8"),
                ) as response:
                    body_parts: list[bytes] = []
                    body_size = 0
                    async for part in response.aiter_bytes():
                        body_size += len(part)
                        if body_size > plan.max_response_bytes:
                            raise DeepSeekHttpTransportError(
                                "provider_response_too_large",
                                latency_ms=_elapsed_ms(started_ns),
                            )
                        body_parts.append(part)
                    body = b"".join(body_parts)
                    return DeepSeekHttpResponse(
                        status_code=response.status_code,
                        body=body,
                        retry_after_ms=_retry_after_ms(
                            response.headers.get("Retry-After"),
                            status_code=response.status_code,
                        ),
                        latency_ms=_elapsed_ms(started_ns),
                    )


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)


def _retry_after_ms(value: str | None, *, status_code: int) -> int | None:
    if status_code != 429 or value is None:
        return None
    try:
        seconds = int(value, 10)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds * 1_000, 120_000)


class _DeepSeekCompletionTokenDetails(FrozenModel):
    reasoning_tokens: Annotated[int | None, Field(ge=0)] = None


class _DeepSeekUsage(FrozenModel):
    completion_tokens: Annotated[int, Field(ge=0)]
    prompt_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_hit_tokens: Annotated[int | None, Field(ge=0)] = None
    prompt_cache_miss_tokens: Annotated[int | None, Field(ge=0)] = None
    completion_tokens_details: _DeepSeekCompletionTokenDetails | None = None

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("DeepSeek usage total does not equal prompt plus completion")
        cache_values = (
            self.prompt_cache_hit_tokens,
            self.prompt_cache_miss_tokens,
        )
        if (cache_values[0] is None) != (cache_values[1] is None):
            raise ValueError("DeepSeek cache usage fields must be reported together")
        if all(value is not None for value in cache_values) and (
            sum(value for value in cache_values if value is not None) != self.prompt_tokens
        ):
            raise ValueError("DeepSeek cache usage does not equal prompt tokens")
        return self


class _DeepSeekToolFunction(FrozenModel):
    name: str
    arguments: str


class _DeepSeekToolCall(FrozenModel):
    id: str
    type: Literal["function"]
    function: _DeepSeekToolFunction


class _DeepSeekAssistantMessage(FrozenModel):
    content: Annotated[str | None, Field(max_length=2_000_000)]
    role: Literal["assistant"]
    reasoning_content: Annotated[str | None, Field(max_length=2_000_000)] = None
    tool_calls: tuple[_DeepSeekToolCall, ...] | None = None

    @field_validator("tool_calls", mode="before")
    @classmethod
    def parse_tool_calls(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, list | tuple):
            raise ValueError("DeepSeek tool_calls must be an array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_non_thinking_contract(self) -> Self:
        if self.reasoning_content not in {None, ""}:
            raise ValueError("thinking-disabled response contains reasoning content")
        # DeepSeek may serialize an empty array for a tool-disabled response.
        # Empty and absent both mean that no call occurred; any actual call is
        # still a frozen request-profile violation.
        if self.tool_calls not in (None, ()):
            raise ValueError("tool-disabled response contains tool_calls")
        return self


class _DeepSeekChoice(FrozenModel):
    finish_reason: Literal[
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
    ]
    index: Literal[0]
    message: _DeepSeekAssistantMessage
    logprobs: None = None


class _DeepSeekChatCompletion(FrozenModel):
    id: Annotated[str, Field(min_length=1, max_length=500)]
    choices: Annotated[tuple[_DeepSeekChoice, ...], Field(min_length=1, max_length=1)]
    created: Annotated[int, Field(ge=0)]
    model: Literal["deepseek-v4-pro"]
    object: Literal["chat.completion"]
    system_fingerprint: Annotated[str | None, Field(max_length=500)] = None
    usage: _DeepSeekUsage | None = None

    @field_validator("choices", mode="before")
    @classmethod
    def parse_choices(cls, value: object) -> tuple[object, ...]:  # type: ignore[valid-type]
        if not isinstance(value, list | tuple):
            raise ValueError("DeepSeek choices must be an array")
        return tuple(value)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("DeepSeek response id must be a trimmed single line")
        return value

    @field_validator("system_fingerprint")
    @classmethod
    def validate_system_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("DeepSeek system fingerprint must be a trimmed single line")
        return value


@dataclass(frozen=True)
class ParsedDeepSeekChatCompletion:
    response: _DeepSeekChatCompletion
    raw_completion: AITagRawCompletion
    ignored_usage_extension_count: int


DEEPSEEK_OUTER_RESPONSE_DIAGNOSTIC_SCHEMA_VERSION = "deepseek-outer-response-diagnostic-v2"
DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION = "deepseek-outer-response-parser-v2"
_CURRENT_DEEPSEEK_PROVIDER_CONTRACT_SNAPSHOT = "deepseek-chat-completions-2026-07-19-r3"
_MAX_IGNORED_USAGE_EXTENSION_FIELDS = 16
_DEEPSEEK_USAGE_KNOWN_FIELDS = frozenset(
    (
        "completion_tokens",
        "prompt_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "completion_tokens_details",
    )
)

DeepSeekOuterResponseStage = Literal["utf8", "json", "top_level", "schema"]
DeepSeekOuterResponseErrorType = Literal[
    "invalid_utf8",
    "invalid_json_syntax",
    "duplicate_json_key",
    "non_finite_json_number",
    "json_nesting_too_deep",
    "top_level_not_object",
    "missing_field",
    "unknown_field",
    "unexpected_literal",
    "type_mismatch",
    "constraint_violation",
    "contract_violation",
    "schema_validation_error",
]

_OUTER_RESPONSE_SCHEMA_FIELDS = frozenset(
    (
        "arguments",
        "choices",
        "completion_tokens",
        "completion_tokens_details",
        "content",
        "created",
        "finish_reason",
        "function",
        "id",
        "index",
        "logprobs",
        "message",
        "model",
        "name",
        "object",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "prompt_tokens",
        "reasoning_content",
        "reasoning_tokens",
        "role",
        "system_fingerprint",
        "tool_calls",
        "total_tokens",
        "type",
        "usage",
    )
)
_OUTER_RESPONSE_MAX_PATH_COMPONENTS = 12
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHADOW_PLAN_ID_PATTERN = r"^ai-tag-shadow-plan:sha256:[0-9a-f]{64}$"
_OUTER_DIAGNOSTIC_ID_PATTERN = r"^deepseek-outer-response-diagnostic:sha256:[0-9a-f]{64}$"
_SAFE_PATH_MARKERS = frozenset(("$", "<unknown-field>", "<unknown-location>", "<truncated>"))
_STAGE_ERROR_TYPES: dict[str, frozenset[str]] = {
    "utf8": frozenset(("invalid_utf8",)),
    "json": frozenset(
        (
            "invalid_json_syntax",
            "duplicate_json_key",
            "non_finite_json_number",
            "json_nesting_too_deep",
        )
    ),
    "top_level": frozenset(("top_level_not_object",)),
    "schema": frozenset(
        (
            "missing_field",
            "unknown_field",
            "unexpected_literal",
            "type_mismatch",
            "constraint_violation",
            "contract_violation",
            "schema_validation_error",
        )
    ),
}


class DeepSeekOuterResponseDiagnostic(FrozenModel):
    """Bounded provider-contract diagnostic that never retains response values."""

    schema_version: Literal[
        "deepseek-outer-response-diagnostic-v1",
        "deepseek-outer-response-diagnostic-v2",
    ]
    parser_contract_version: Literal[
        "deepseek-outer-response-parser-v1",
        "deepseek-outer-response-parser-v2",
    ]
    plan_id: Annotated[str, Field(pattern=_SHADOW_PLAN_ID_PATTERN)]
    response_body_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    response_body_size_bytes: Annotated[int, Field(ge=0, le=8_000_000)]
    stage: DeepSeekOuterResponseStage
    error_type: DeepSeekOuterResponseErrorType
    field_path: Annotated[
        tuple[str, ...],
        Field(min_length=1, max_length=_OUTER_RESPONSE_MAX_PATH_COMPONENTS),
    ]
    qualification: Literal["privacy_safe_structure_only_not_provider_truth"]
    diagnostic_id: Annotated[str, Field(pattern=_OUTER_DIAGNOSTIC_ID_PATTERN)]

    @field_validator("field_path", mode="before")
    @classmethod
    def parse_field_path(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    @field_validator("field_path")
    @classmethod
    def validate_safe_field_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value[0] != "$":
            raise ValueError("invalid DeepSeek outer response diagnostic field path")
        for component in value:
            if (
                component not in _SAFE_PATH_MARKERS
                and component not in _OUTER_RESPONSE_SCHEMA_FIELDS
                and not (
                    component.startswith("[")
                    and component.endswith("]")
                    and component[1:-1].isdigit()
                    and 0 <= int(component[1:-1]) <= 999
                )
            ):
                raise ValueError("unsafe DeepSeek outer response diagnostic field path")
        return value

    @model_validator(mode="after")
    def validate_contract_and_identity(self) -> Self:
        expected_parser_contract = {
            "deepseek-outer-response-diagnostic-v1": "deepseek-outer-response-parser-v1",
            "deepseek-outer-response-diagnostic-v2": "deepseek-outer-response-parser-v2",
        }[self.schema_version]
        if self.parser_contract_version != expected_parser_contract:
            raise ValueError("DeepSeek diagnostic schema and parser contract versions differ")
        if self.error_type not in _STAGE_ERROR_TYPES[self.stage]:
            raise ValueError("DeepSeek outer response diagnostic stage and error type differ")
        if self.stage != "schema" and self.field_path != ("$",):
            raise ValueError("non-schema DeepSeek diagnostic cannot carry a field path")
        payload = self.model_dump(mode="json", exclude={"diagnostic_id"})
        expected = canonical_hash("deepseek-outer-response-diagnostic", payload)
        if self.diagnostic_id != expected:
            raise ValueError("DeepSeek outer response diagnostic ID does not match its contents")
        return self


class DeepSeekOuterResponseError(ValueError):
    def __init__(self, diagnostic: DeepSeekOuterResponseDiagnostic) -> None:
        super().__init__("DeepSeek outer response violates the frozen provider contract")
        self.diagnostic = diagnostic


def load_deepseek_outer_response_diagnostic(
    raw: str | bytes,
) -> DeepSeekOuterResponseDiagnostic:
    try:
        return load_json_model(
            raw,
            DeepSeekOuterResponseDiagnostic,
            "DeepSeek outer response diagnostic",
        )
    except (TypeError, ValueError):
        raise ValueError("invalid DeepSeek outer response diagnostic") from None


class _DeepSeekDuplicateJsonKeyError(ValueError):
    pass


class _DeepSeekNonFiniteJsonNumberError(ValueError):
    pass


def _build_outer_response_diagnostic(
    *,
    plan: AITagShadowDispatchPlan,
    raw_body: bytes,
    stage: DeepSeekOuterResponseStage,
    error_type: DeepSeekOuterResponseErrorType,
    field_path: tuple[str, ...] = ("$",),
) -> DeepSeekOuterResponseDiagnostic:
    payload: dict[str, object] = {
        "schema_version": DEEPSEEK_OUTER_RESPONSE_DIAGNOSTIC_SCHEMA_VERSION,
        "parser_contract_version": DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION,
        "plan_id": plan.plan_id,
        "response_body_sha256": "sha256:" + hashlib.sha256(raw_body).hexdigest(),
        "response_body_size_bytes": len(raw_body),
        "stage": stage,
        "error_type": error_type,
        "field_path": field_path,
        "qualification": "privacy_safe_structure_only_not_provider_truth",
    }
    payload["diagnostic_id"] = canonical_hash("deepseek-outer-response-diagnostic", payload)
    return DeepSeekOuterResponseDiagnostic.model_validate(payload)


def _raise_outer_response_error(
    *,
    plan: AITagShadowDispatchPlan,
    raw_body: bytes,
    stage: DeepSeekOuterResponseStage,
    error_type: DeepSeekOuterResponseErrorType,
    field_path: tuple[str, ...] = ("$",),
) -> NoReturn:
    raise DeepSeekOuterResponseError(
        _build_outer_response_diagnostic(
            plan=plan,
            raw_body=raw_body,
            stage=stage,
            error_type=error_type,
            field_path=field_path,
        )
    ) from None


def _reject_provider_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DeepSeekDuplicateJsonKeyError
        result[key] = value
    return result


def _reject_provider_non_finite_number(_value: str) -> object:
    raise _DeepSeekNonFiniteJsonNumberError


def _parse_provider_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _DeepSeekNonFiniteJsonNumberError
    return parsed


def _load_deepseek_outer_object(
    raw_body: bytes,
    *,
    plan: AITagShadowDispatchPlan,
) -> dict[str, object]:
    if not isinstance(raw_body, bytes):
        raise TypeError("DeepSeek raw response body must use bytes")
    decode_error: DeepSeekOuterResponseErrorType | None = None
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        decode_error = "invalid_utf8"
    if decode_error is not None:
        _raise_outer_response_error(
            plan=plan,
            raw_body=raw_body,
            stage="utf8",
            error_type=decode_error,
        )
    json_error: DeepSeekOuterResponseErrorType | None = None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_provider_duplicate_keys,
            parse_constant=_reject_provider_non_finite_number,
            parse_float=_parse_provider_float,
        )
    except _DeepSeekDuplicateJsonKeyError:
        json_error = "duplicate_json_key"
    except _DeepSeekNonFiniteJsonNumberError:
        json_error = "non_finite_json_number"
    except json.JSONDecodeError:
        json_error = "invalid_json_syntax"
    except RecursionError:
        json_error = "json_nesting_too_deep"
    except ValueError:
        # CPython can reject otherwise well-formed JSON integers that exceed its
        # configured digit limit.  Keep every decoder rejection inside the same
        # value-free, content-addressed diagnostic boundary.
        json_error = "invalid_json_syntax"
    if json_error is not None:
        _raise_outer_response_error(
            plan=plan,
            raw_body=raw_body,
            stage="json",
            error_type=json_error,
        )
    if not isinstance(payload, dict):
        _raise_outer_response_error(
            plan=plan,
            raw_body=raw_body,
            stage="top_level",
            error_type="top_level_not_object",
        )
    return payload


def _schema_error_type(raw_error_type: str) -> DeepSeekOuterResponseErrorType:
    if raw_error_type == "missing":
        return "missing_field"
    if raw_error_type == "extra_forbidden":
        return "unknown_field"
    if raw_error_type == "literal_error":
        return "unexpected_literal"
    if raw_error_type == "value_error":
        return "contract_violation"
    if raw_error_type == "none_required":
        return "contract_violation"
    if raw_error_type.endswith(("_type", "_parsing")):
        return "type_mismatch"
    if raw_error_type in {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "string_too_long",
        "string_too_short",
        "too_long",
        "too_short",
    }:
        return "constraint_violation"
    return "schema_validation_error"


def _safe_schema_field_path(location: tuple[object, ...]) -> tuple[str, ...]:
    path = ["$"]
    max_location_components = _OUTER_RESPONSE_MAX_PATH_COMPONENTS - 1
    for component in location[:max_location_components]:
        if isinstance(component, str):
            path.append(
                component if component in _OUTER_RESPONSE_SCHEMA_FIELDS else "<unknown-field>"
            )
        elif type(component) is int and 0 <= component <= 999:
            path.append(f"[{component}]")
        else:
            path.append("<unknown-location>")
    if len(location) > max_location_components:
        path[-1] = "<truncated>"
    return tuple(path)


def _schema_diagnostic_parts(
    error: ValidationError,
) -> tuple[DeepSeekOuterResponseErrorType, tuple[str, ...]]:
    details = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    if not details:
        return "schema_validation_error", ("$",)
    first = details[0]
    raw_error_type = first.get("type")
    location = first.get("loc")
    return (
        (
            _schema_error_type(raw_error_type)
            if isinstance(raw_error_type, str)
            else "schema_validation_error"
        ),
        (_safe_schema_field_path(location) if isinstance(location, tuple) else ("$",)),
    )


def _normalize_usage_extensions(
    payload: dict[str, object],
    *,
    plan: AITagShadowDispatchPlan,
    raw_body: bytes,
) -> tuple[dict[str, object], int]:
    """Drop only bounded direct ``usage`` extensions and retain no names or values."""

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return payload, 0
    unknown_count = sum(key not in _DEEPSEEK_USAGE_KNOWN_FIELDS for key in usage)
    if unknown_count > _MAX_IGNORED_USAGE_EXTENSION_FIELDS:
        _raise_outer_response_error(
            plan=plan,
            raw_body=raw_body,
            stage="schema",
            error_type="constraint_violation",
            field_path=("$", "usage"),
        )
    if unknown_count == 0:
        return payload, 0
    normalized_payload = dict(payload)
    normalized_payload["usage"] = {
        key: value for key, value in usage.items() if key in _DEEPSEEK_USAGE_KNOWN_FIELDS
    }
    return normalized_payload, unknown_count


def parse_deepseek_chat_completion(
    raw_body: bytes,
    *,
    plan: AITagShadowDispatchPlan,
    latency_ms: int,
) -> ParsedDeepSeekChatCompletion:
    """Parse one provider outer response without promoting it to a formal result."""

    if (
        plan.shadow_provider_policy.provider_contract_snapshot
        != _CURRENT_DEEPSEEK_PROVIDER_CONTRACT_SNAPSHOT
    ):
        raise ValueError(
            "DeepSeek outer response parser requires the current provider contract snapshot"
        ) from None
    payload = _load_deepseek_outer_object(raw_body, plan=plan)
    payload, ignored_usage_extension_count = _normalize_usage_extensions(
        payload,
        plan=plan,
        raw_body=raw_body,
    )
    diagnostic_parts: tuple[DeepSeekOuterResponseErrorType, tuple[str, ...]] | None = None
    try:
        response = _DeepSeekChatCompletion.model_validate(payload)
    except ValidationError as exc:
        diagnostic_parts = _schema_diagnostic_parts(exc)
    except (TypeError, ValueError):
        diagnostic_parts = "schema_validation_error", ("$",)
    if diagnostic_parts is not None:
        error_type, field_path = diagnostic_parts
        _raise_outer_response_error(
            plan=plan,
            raw_body=raw_body,
            stage="schema",
            error_type=error_type,
            field_path=field_path,
        )
    choice = response.choices[0]
    usage = response.usage
    raw_usage = None
    if usage is not None:
        raw_usage = AITagRawUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
        )
    raw_completion = AITagRawCompletion(
        source_kind="unverified_raw",
        content=choice.message.content,
        finish_reason=choice.finish_reason,
        model=response.model,
        system_fingerprint=response.system_fingerprint,
        usage=raw_usage,
        latency_ms=latency_ms,
        attempt_count=plan.max_attempts,
    )
    return ParsedDeepSeekChatCompletion(
        response=response,
        raw_completion=raw_completion,
        ignored_usage_extension_count=ignored_usage_extension_count,
    )


def observe_deepseek_chat_completion(
    *,
    plan: AITagShadowDispatchPlan,
    attempt_receipt: AITagDispatchAttemptReceipt,
    raw_body: bytes,
) -> tuple[ParsedDeepSeekChatCompletion, AITagObservedProviderResponseReceiptV2]:
    parsed = parse_deepseek_chat_completion(
        raw_body,
        plan=plan,
        latency_ms=attempt_receipt.latency_ms,
    )
    raw_hash = "sha256:" + hashlib.sha256(raw_body).hexdigest()
    if (
        attempt_receipt.plan_id != plan.plan_id
        or attempt_receipt.transport_status != "response_received"
        or attempt_receipt.http_status != 200
        or attempt_receipt.response_body_sha256 != raw_hash
        or attempt_receipt.response_body_size_bytes != len(raw_body)
    ):
        raise ValueError("attempt receipt does not bind this successful raw response")
    response = parsed.response
    choice = response.choices[0]
    content_bytes = (
        b"<null-content>"
        if choice.message.content is None
        else choice.message.content.encode("utf-8")
    )
    qualification = (
        "observed_over_tls_not_provider_signed"
        if attempt_receipt.transport_evidence == "httpx_tls_fixed_endpoint"
        else "synthetic_or_untrusted_transport_not_provider_observation"
    )
    receipt = seal_ai_tag_observed_provider_response_receipt_v2(
        {
            "schema_version": AI_TAG_OBSERVED_RESPONSE_RECEIPT_V2_SCHEMA_VERSION,
            "provider_contract_snapshot": _CURRENT_DEEPSEEK_PROVIDER_CONTRACT_SNAPSHOT,
            "outer_parser_contract_version": DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION,
            "usage_extension_policy": "direct_unknown_usage_fields_discarded-v1",
            "plan_id": plan.plan_id,
            "attempt_receipt_id": attempt_receipt.receipt_id,
            "http_status": 200,
            "response_body_sha256": raw_hash,
            "response_body_size_bytes": len(raw_body),
            "provider_response_id": response.id,
            "response_object": response.object,
            "created": response.created,
            "model": response.model,
            "system_fingerprint": response.system_fingerprint,
            "choice_count": len(response.choices),
            "selected_choice_index": choice.index,
            "message_role": choice.message.role,
            "finish_reason": choice.finish_reason,
            "content_sha256": ("sha256:" + hashlib.sha256(content_bytes).hexdigest()),
            "usage": parsed.raw_completion.usage,
            "ignored_usage_extension_count": parsed.ignored_usage_extension_count,
            "usage_extension_disposition": (
                "none_observed"
                if parsed.ignored_usage_extension_count == 0
                else "discarded_without_name_or_value_retention"
            ),
            "transport_evidence": attempt_receipt.transport_evidence,
            "qualification": qualification,
        }
    )
    return parsed, receipt


def verify_deepseek_observed_provider_response_receipt(
    receipt: AITagObservedProviderResponseReceiptV2,
    *,
    plan: AITagShadowDispatchPlan,
    attempt_receipt: AITagDispatchAttemptReceipt,
    raw_body: bytes,
) -> None:
    _, expected = observe_deepseek_chat_completion(
        plan=plan,
        attempt_receipt=attempt_receipt,
        raw_body=raw_body,
    )
    actual = AITagObservedProviderResponseReceiptV2.model_validate(receipt.model_dump(mode="json"))
    if actual != expected:
        raise ValueError("observed response receipt differs from trusted raw-response rebuild")


__all__ = [
    "DEEPSEEK_OUTER_RESPONSE_DIAGNOSTIC_SCHEMA_VERSION",
    "DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION",
    "DeepSeekCredentialProvider",
    "DeepSeekCredentialUnavailableError",
    "DeepSeekHttpResponse",
    "DeepSeekHttpTransportError",
    "DeepSeekOuterResponseDiagnostic",
    "DeepSeekOuterResponseError",
    "DeepSeekOuterResponseErrorType",
    "DeepSeekOuterResponseStage",
    "DeepSeekShadowHttpTransport",
    "EnvironmentDeepSeekCredentialProvider",
    "ParsedDeepSeekChatCompletion",
    "load_deepseek_outer_response_diagnostic",
    "observe_deepseek_chat_completion",
    "parse_deepseek_chat_completion",
    "verify_deepseek_observed_provider_response_receipt",
]
