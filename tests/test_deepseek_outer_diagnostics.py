from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from arkts_code_reviewer.hybrid_analysis._canonical import canonical_hash
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DEEPSEEK_OUTER_RESPONSE_DIAGNOSTIC_SCHEMA_VERSION,
    DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION,
    DeepSeekOuterResponseError,
    load_deepseek_outer_response_diagnostic,
    parse_deepseek_chat_completion,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import AITagShadowDispatchPlan


class _PlanStub:
    max_attempts = 1

    def __init__(
        self,
        marker: str = "a",
        *,
        provider_contract_snapshot: str = "deepseek-chat-completions-2026-07-19-r3",
    ) -> None:
        self.plan_id = "ai-tag-shadow-plan:sha256:" + marker * 64
        self.shadow_provider_policy = _ProviderPolicyStub(provider_contract_snapshot)


class _ProviderPolicyStub:
    def __init__(self, provider_contract_snapshot: str) -> None:
        self.provider_contract_snapshot = provider_contract_snapshot


def _plan_stub(marker: str = "a") -> AITagShadowDispatchPlan:
    return cast(AITagShadowDispatchPlan, _PlanStub(marker))


def _valid_outer_payload() -> dict[str, object]:
    return {
        "id": "chatcmpl-diagnostic-test",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": '{"judgments":[]}',
                    "role": "assistant",
                    "reasoning_content": None,
                },
                "logprobs": None,
            }
        ],
        "created": 1_750_000_000,
        "model": "deepseek-v4-pro",
        "object": "chat.completion",
        "system_fingerprint": "fp-diagnostic-test",
        "usage": {
            "completion_tokens": 50,
            "prompt_tokens": 100,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 25,
            "prompt_cache_miss_tokens": 75,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _encoded(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _capture(
    raw_body: bytes,
    *,
    plan: AITagShadowDispatchPlan | None = None,
) -> DeepSeekOuterResponseError:
    plan = _plan_stub() if plan is None else plan
    with pytest.raises(DeepSeekOuterResponseError) as raised:
        parse_deepseek_chat_completion(
            raw_body,
            plan=plan,
            latency_ms=1,
        )
    error = raised.value
    assert str(error) == "DeepSeek outer response violates the frozen provider contract"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.diagnostic.schema_version == (DEEPSEEK_OUTER_RESPONSE_DIAGNOSTIC_SCHEMA_VERSION)
    assert error.diagnostic.parser_contract_version == (
        DEEPSEEK_OUTER_RESPONSE_PARSER_CONTRACT_VERSION
    )
    assert error.diagnostic.plan_id == plan.plan_id
    assert error.diagnostic.response_body_sha256 == (
        "sha256:" + hashlib.sha256(raw_body).hexdigest()
    )
    assert error.diagnostic.response_body_size_bytes == len(raw_body)
    assert error.diagnostic.qualification == ("privacy_safe_structure_only_not_provider_truth")
    assert (
        load_deepseek_outer_response_diagnostic(error.diagnostic.model_dump_json())
        == error.diagnostic
    )
    return error


@pytest.mark.parametrize(
    ("raw_body", "stage", "error_type"),
    [
        (b"\xffPRIVATE_UTF8", "utf8", "invalid_utf8"),
        (b'{"id":"PRIVATE_JSON"', "json", "invalid_json_syntax"),
        (
            b'{"id":"PRIVATE_FIRST","id":"PRIVATE_SECOND"}',
            "json",
            "duplicate_json_key",
        ),
        (b'{"PRIVATE_NUMBER":NaN}', "json", "non_finite_json_number"),
        (b'["PRIVATE_TOP_LEVEL"]', "top_level", "top_level_not_object"),
    ],
)
def test_outer_diagnostics_distinguish_decode_json_and_top_level_without_values(
    raw_body: bytes,
    stage: str,
    error_type: str,
) -> None:
    error = _capture(raw_body)

    assert error.diagnostic.stage == stage
    assert error.diagnostic.error_type == error_type
    assert error.diagnostic.field_path == ("$",)
    safe_output = f"{error!r}|{error.diagnostic!r}|{error.diagnostic.model_dump_json()}"
    assert "PRIVATE" not in safe_output
    assert raw_body.decode("utf-8", errors="ignore") not in safe_output


def test_overlong_json_integer_stays_inside_the_value_free_diagnostic_boundary() -> None:
    raw_body = b'{"private_marker":"PRIVATE_OVERLONG_INTEGER","n":' + b"9" * 5_000 + b"}"

    error = _capture(raw_body)

    assert error.diagnostic.stage == "json"
    assert error.diagnostic.error_type == "invalid_json_syntax"
    assert error.diagnostic.field_path == ("$",)
    safe_output = f"{error!r}|{error.diagnostic!r}|{error.diagnostic.model_dump_json()}"
    assert "PRIVATE_OVERLONG_INTEGER" not in safe_output
    assert "Exceeds the limit" not in safe_output


@pytest.mark.parametrize(
    ("mutation", "error_type", "field_path"),
    [
        ("missing_id", "missing_field", ("$", "id")),
        ("wrong_model", "unexpected_literal", ("$", "model")),
        ("wrong_created_type", "type_mismatch", ("$", "created")),
        ("wrong_usage_type", "type_mismatch", ("$", "usage")),
        ("short_id", "constraint_violation", ("$", "id")),
        ("usage_arithmetic", "contract_violation", ("$", "usage")),
        ("unknown_field", "unknown_field", ("$", "<unknown-field>")),
        (
            "wrong_message_role",
            "unexpected_literal",
            ("$", "choices", "[0]", "message", "role"),
        ),
    ],
)
def test_schema_diagnostic_exposes_only_bounded_type_and_safe_field_path(
    mutation: str,
    error_type: str,
    field_path: tuple[str, ...],
) -> None:
    payload = _valid_outer_payload()
    if mutation == "missing_id":
        payload.pop("id")
    elif mutation == "wrong_model":
        payload["model"] = "PRIVATE_MODEL_VALUE"
    elif mutation == "wrong_created_type":
        payload["created"] = "PRIVATE_CREATED_VALUE"
    elif mutation == "wrong_usage_type":
        payload["usage"] = "PRIVATE_USAGE_VALUE"
    elif mutation == "short_id":
        payload["id"] = ""
    elif mutation == "usage_arithmetic":
        usage = cast(dict[str, object], payload["usage"])
        usage["total_tokens"] = 151
    elif mutation == "unknown_field":
        payload["PRIVATE_UNKNOWN_FIELD_NAME"] = "PRIVATE_UNKNOWN_FIELD_VALUE"
    elif mutation == "wrong_message_role":
        choices = cast(list[dict[str, object]], payload["choices"])
        message = cast(dict[str, object], choices[0]["message"])
        message["role"] = "PRIVATE_ROLE_VALUE"
    else:  # pragma: no cover - closed parameter table
        raise AssertionError("unknown test mutation")

    error = _capture(_encoded(payload))

    assert error.diagnostic.stage == "schema"
    assert error.diagnostic.error_type == error_type
    assert error.diagnostic.field_path == field_path
    safe_output = f"{error!r}|{error.diagnostic!r}|{error.diagnostic.model_dump_json()}"
    assert "PRIVATE" not in safe_output
    assert "total_tokens" not in safe_output


@pytest.mark.parametrize("extension_count", [1, 16])
def test_direct_usage_extensions_are_counted_then_discarded_without_retention(
    extension_count: int,
) -> None:
    payload = _valid_outer_payload()
    usage = cast(dict[str, object], payload["usage"])
    for index in range(extension_count):
        usage[f"PRIVATE_USAGE_EXTENSION_{index}"] = {
            "PRIVATE_VALUE": f"PRIVATE_SECRET_{index}"
        }

    parsed = parse_deepseek_chat_completion(
        _encoded(payload),
        plan=_plan_stub(),
        latency_ms=1,
    )

    assert parsed.ignored_usage_extension_count == extension_count
    assert parsed.response.usage is not None
    assert parsed.response.usage.total_tokens == 150
    retained = "|".join(
        (
            repr(parsed),
            repr(parsed.raw_completion),
            json.dumps(parsed.response.model_dump(mode="json"), sort_keys=True),
        )
    )
    assert "PRIVATE_USAGE_EXTENSION" not in retained
    assert "PRIVATE_SECRET" not in retained


def test_usage_extension_limit_fails_closed_without_retaining_names_or_values() -> None:
    payload = _valid_outer_payload()
    usage = cast(dict[str, object], payload["usage"])
    for index in range(17):
        usage[f"PRIVATE_USAGE_EXTENSION_{index}"] = f"PRIVATE_VALUE_{index}"

    error = _capture(_encoded(payload))

    assert error.diagnostic.stage == "schema"
    assert error.diagnostic.error_type == "constraint_violation"
    assert error.diagnostic.field_path == ("$", "usage")
    assert "PRIVATE" not in error.diagnostic.model_dump_json()


def test_usage_compatibility_does_not_relax_known_or_nested_usage_contracts() -> None:
    known_invalid = _valid_outer_payload()
    known_usage = cast(dict[str, object], known_invalid["usage"])
    known_usage["PRIVATE_EXTENSION"] = "PRIVATE_VALUE"
    known_usage["total_tokens"] = 151
    known_error = _capture(_encoded(known_invalid))
    assert known_error.diagnostic.error_type == "contract_violation"
    assert known_error.diagnostic.field_path == ("$", "usage")

    nested_invalid = _valid_outer_payload()
    nested_usage = cast(dict[str, object], nested_invalid["usage"])
    details = cast(dict[str, object], nested_usage["completion_tokens_details"])
    details["PRIVATE_NESTED_EXTENSION"] = "PRIVATE_VALUE"
    nested_error = _capture(_encoded(nested_invalid))
    assert nested_error.diagnostic.error_type == "unknown_field"
    assert nested_error.diagnostic.field_path == (
        "$",
        "usage",
        "completion_tokens_details",
        "<unknown-field>",
    )

    typed_invalid = _valid_outer_payload()
    typed_usage = cast(dict[str, object], typed_invalid["usage"])
    typed_usage["PRIVATE_EXTENSION"] = "PRIVATE_VALUE"
    typed_usage["prompt_tokens"] = True
    typed_error = _capture(_encoded(typed_invalid))
    assert typed_error.diagnostic.error_type == "type_mismatch"
    assert typed_error.diagnostic.field_path == ("$", "usage", "prompt_tokens")


def test_usage_extension_does_not_hide_an_unknown_field_outside_usage() -> None:
    payload = _valid_outer_payload()
    usage = cast(dict[str, object], payload["usage"])
    usage["PRIVATE_USAGE_EXTENSION"] = "PRIVATE_USAGE_VALUE"
    payload["PRIVATE_TOP_LEVEL_EXTENSION"] = "PRIVATE_TOP_LEVEL_VALUE"

    error = _capture(_encoded(payload))

    assert error.diagnostic.error_type == "unknown_field"
    assert error.diagnostic.field_path == ("$", "<unknown-field>")
    assert "PRIVATE" not in error.diagnostic.model_dump_json()

    message_payload = _valid_outer_payload()
    message_usage = cast(dict[str, object], message_payload["usage"])
    message_usage["PRIVATE_USAGE_EXTENSION"] = "PRIVATE_USAGE_VALUE"
    choices = cast(list[dict[str, object]], message_payload["choices"])
    message = cast(dict[str, object], choices[0]["message"])
    message["PRIVATE_MESSAGE_EXTENSION"] = "PRIVATE_MESSAGE_VALUE"
    message_error = _capture(_encoded(message_payload))
    assert message_error.diagnostic.error_type == "unknown_field"
    assert message_error.diagnostic.field_path == (
        "$",
        "choices",
        "[0]",
        "message",
        "<unknown-field>",
    )


def test_duplicate_key_inside_ignored_usage_value_remains_a_global_json_error() -> None:
    raw_body = _encoded(_valid_outer_payload()).replace(
        b'"usage":{',
        (
            b'"usage":{"PRIVATE_EXTENSION":'
            b'{"PRIVATE_DUPLICATE":1,"PRIVATE_DUPLICATE":2},'
        ),
        1,
    )

    error = _capture(raw_body)

    assert error.diagnostic.stage == "json"
    assert error.diagnostic.error_type == "duplicate_json_key"
    assert error.diagnostic.field_path == ("$",)
    assert "PRIVATE" not in error.diagnostic.model_dump_json()


def test_non_finite_usage_extension_value_is_rejected_before_normalization() -> None:
    raw_body = _encoded(_valid_outer_payload()).replace(
        b'"usage":{',
        b'"usage":{"PRIVATE_OVERFLOW":1e400,',
        1,
    )

    error = _capture(raw_body)

    assert error.diagnostic.stage == "json"
    assert error.diagnostic.error_type == "non_finite_json_number"
    assert error.diagnostic.field_path == ("$",)
    assert "PRIVATE_OVERFLOW" not in error.diagnostic.model_dump_json()


def test_non_bytes_input_is_rejected_before_provider_response_diagnostics() -> None:
    private_input = "PRIVATE_NON_BYTES_INPUT"

    with pytest.raises(TypeError) as raised:
        parse_deepseek_chat_completion(
            cast(bytes, private_input),
            plan=_plan_stub(),
            latency_ms=1,
        )

    assert str(raised.value) == "DeepSeek raw response body must use bytes"
    assert private_input not in repr(raised.value)


def test_outer_parser_rejects_a_historical_provider_snapshot_before_reading_body() -> None:
    private_body = b'{"PRIVATE_HISTORICAL_RESPONSE":true}'
    historical_plan = cast(
        AITagShadowDispatchPlan,
        _PlanStub(
            provider_contract_snapshot="deepseek-chat-completions-2026-07-18-r2",
        ),
    )

    with pytest.raises(ValueError) as raised:
        parse_deepseek_chat_completion(
            private_body,
            plan=historical_plan,
            latency_ms=1,
        )

    assert str(raised.value) == (
        "DeepSeek outer response parser requires the current provider contract snapshot"
    )
    assert "PRIVATE_HISTORICAL_RESPONSE" not in repr(raised.value)
    assert raised.value.__cause__ is None


def test_empty_tool_calls_is_normalized_as_no_call_but_nonempty_remains_invalid() -> None:
    empty_payload = _valid_outer_payload()
    empty_choices = cast(list[dict[str, object]], empty_payload["choices"])
    empty_message = cast(dict[str, object], empty_choices[0]["message"])
    empty_message["tool_calls"] = []

    parsed = parse_deepseek_chat_completion(
        _encoded(empty_payload),
        plan=_plan_stub(),
        latency_ms=1,
    )

    assert parsed.response.choices[0].message.tool_calls == ()

    nonempty_payload = _valid_outer_payload()
    nonempty_choices = cast(list[dict[str, object]], nonempty_payload["choices"])
    nonempty_message = cast(dict[str, object], nonempty_choices[0]["message"])
    nonempty_message["tool_calls"] = [
        {
            "id": "PRIVATE_TOOL_CALL_ID",
            "type": "function",
            "function": {
                "name": "PRIVATE_TOOL_NAME",
                "arguments": "PRIVATE_TOOL_ARGUMENTS",
            },
        }
    ]

    error = _capture(_encoded(nonempty_payload))

    assert error.diagnostic.stage == "schema"
    assert error.diagnostic.error_type == "contract_violation"
    assert error.diagnostic.field_path == ("$", "choices", "[0]", "message")
    rendered = error.diagnostic.model_dump_json()
    assert "PRIVATE" not in rendered


def test_unrequested_logprobs_is_a_value_free_request_profile_diagnostic() -> None:
    payload = _valid_outer_payload()
    choices = cast(list[dict[str, object]], payload["choices"])
    choices[0]["logprobs"] = {
        "content": [
            {
                "token": "PRIVATE_TOKEN",
                "logprob": -0.1,
                "bytes": [80],
                "top_logprobs": [],
            }
        ]
    }

    error = _capture(_encoded(payload))

    assert error.diagnostic.error_type == "contract_violation"
    assert error.diagnostic.field_path == ("$", "choices", "[0]", "logprobs")
    assert "PRIVATE_TOKEN" not in error.diagnostic.model_dump_json()


def test_diagnostic_identity_binds_plan_body_and_parser_contract() -> None:
    raw_body = b'{"PRIVATE_ONE":true}'
    first = _capture(raw_body).diagnostic
    second_plan = _plan_stub("b")
    second = _capture(raw_body, plan=second_plan).diagnostic
    third = _capture(b'{"PRIVATE_TWO":true}').diagnostic

    assert first.diagnostic_id != second.diagnostic_id
    assert first.diagnostic_id != third.diagnostic_id
    assert first.response_body_sha256 == second.response_body_sha256
    assert first.parser_contract_version == second.parser_contract_version

    tampered = first.model_dump(mode="json")
    tampered["response_body_size_bytes"] = first.response_body_size_bytes + 1
    with pytest.raises(ValueError, match="diagnostic"):
        load_deepseek_outer_response_diagnostic(json.dumps(tampered))


def test_historical_diagnostic_remains_loadable_but_versions_cannot_cross() -> None:
    current = _capture(b'{"PRIVATE_CURRENT":true}').diagnostic
    historical_payload = current.model_dump(mode="json", exclude={"diagnostic_id"})
    historical_payload["schema_version"] = "deepseek-outer-response-diagnostic-v1"
    historical_payload["parser_contract_version"] = "deepseek-outer-response-parser-v1"
    historical_payload["diagnostic_id"] = canonical_hash(
        "deepseek-outer-response-diagnostic",
        historical_payload,
    )

    historical = load_deepseek_outer_response_diagnostic(json.dumps(historical_payload))

    assert historical.schema_version == "deepseek-outer-response-diagnostic-v1"
    assert historical.parser_contract_version == "deepseek-outer-response-parser-v1"
    crossed = historical.model_dump(mode="json", exclude={"diagnostic_id"})
    crossed["parser_contract_version"] = "deepseek-outer-response-parser-v2"
    crossed["diagnostic_id"] = canonical_hash("deepseek-outer-response-diagnostic", crossed)
    with pytest.raises(ValueError, match="diagnostic"):
        load_deepseek_outer_response_diagnostic(json.dumps(crossed))


def test_diagnostic_loader_rejects_arbitrary_or_unbounded_field_paths() -> None:
    diagnostic = _capture(_encoded({**_valid_outer_payload(), "PRIVATE_KEY": True})).diagnostic
    payload = diagnostic.model_dump(mode="json")
    payload["field_path"] = ["$", "PRIVATE_PATH_COMPONENT"]
    with pytest.raises(ValueError, match="diagnostic"):
        load_deepseek_outer_response_diagnostic(json.dumps(payload))

    payload["field_path"] = ["$"] + ["content"] * 12
    with pytest.raises(ValueError, match="diagnostic"):
        load_deepseek_outer_response_diagnostic(json.dumps(payload))


def test_diagnostic_loader_error_does_not_echo_invalid_artifact_values() -> None:
    private_value = "PRIVATE_DIAGNOSTIC_VALUE"

    with pytest.raises(ValueError) as raised:
        load_deepseek_outer_response_diagnostic(
            json.dumps(
                {
                    "schema_version": private_value,
                    "diagnostic_id": private_value,
                }
            )
        )

    assert str(raised.value) == "invalid DeepSeek outer response diagnostic"
    assert private_value not in repr(raised.value)
    assert raised.value.__cause__ is None
