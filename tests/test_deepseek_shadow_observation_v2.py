from __future__ import annotations

import json
from collections.abc import Callable

import pytest

import arkts_code_reviewer.hybrid_analysis.provider_receipts as provider_receipts
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AI_TAG_SHADOW_EXECUTION_OBSERVATION_SCHEMA_VERSION,
    AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION,
    AITagShadowExecutionObservationV2,
    load_ai_tag_shadow_execution_observation_v2,
    seal_ai_tag_shadow_execution_observation,
    seal_ai_tag_shadow_execution_observation_v2,
)


def _id(prefix: str, marker: str) -> str:
    return f"{prefix}:sha256:{marker * 64}"


def _outer_invalid_payload(*, diagnostic_marker: str = "d") -> dict[str, object]:
    return {
        "schema_version": AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION,
        "plan_id": _id("ai-tag-shadow-plan", "a"),
        "claims_id": _id("ai-tag-shadow-claims", "b"),
        "attempt_receipt_id": _id("ai-tag-attempt-receipt", "c"),
        "provider_response_receipt_id": None,
        "response_validation_id": None,
        "outer_diagnostic_id": _id(
            "deepseek-outer-response-diagnostic",
            diagnostic_marker,
        ),
        "status": "invalid_output",
        "reason_code": "provider_outer_contract_invalid",
        "qualification": "unattested_shadow_not_formal",
    }


def _valid_shape_payload() -> dict[str, object]:
    payload = _outer_invalid_payload()
    payload.update(
        {
            "provider_response_receipt_id": _id("ai-tag-observed-response", "e"),
            "response_validation_id": _id("ai-tag-response-validation", "f"),
            "outer_diagnostic_id": None,
            "status": "valid_shape",
            "reason_code": "response_shape_valid",
        }
    )
    return payload


def _inner_invalid_payload() -> dict[str, object]:
    payload = _valid_shape_payload()
    payload.update(
        {
            "status": "invalid_output",
            "reason_code": "schema_invalid",
        }
    )
    return payload


def _provider_failure_payload(status: str) -> dict[str, object]:
    payload = _outer_invalid_payload()
    payload.update(
        {
            "outer_diagnostic_id": None,
            "status": status,
            "reason_code": status,
        }
    )
    return payload


def test_v2_outer_invalid_observation_is_content_addressed_and_round_trips() -> None:
    observation = seal_ai_tag_shadow_execution_observation_v2(_outer_invalid_payload())

    assert isinstance(observation, AITagShadowExecutionObservationV2)
    assert observation.schema_version == "ai-tag-shadow-execution-observation-v2"
    assert observation.outer_diagnostic_id == _id(
        "deepseek-outer-response-diagnostic",
        "d",
    )
    assert observation.observation_id.startswith("ai-tag-shadow-observation:sha256:")
    assert load_ai_tag_shadow_execution_observation_v2(observation.model_dump_json()) == observation

    changed_diagnostic = seal_ai_tag_shadow_execution_observation_v2(
        _outer_invalid_payload(diagnostic_marker="e")
    )
    assert changed_diagnostic.observation_id != observation.observation_id

    tampered = observation.model_dump(mode="json")
    tampered["outer_diagnostic_id"] = _id(
        "deepseek-outer-response-diagnostic",
        "e",
    )
    with pytest.raises(ValueError, match="shadow observation ID"):
        load_ai_tag_shadow_execution_observation_v2(json.dumps(tampered))


def test_v2_contract_has_independent_constant_class_loader_sealer_and_exports() -> None:
    assert AI_TAG_SHADOW_EXECUTION_OBSERVATION_SCHEMA_VERSION == (
        "ai-tag-shadow-execution-observation-v1"
    )
    assert AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION == (
        "ai-tag-shadow-execution-observation-v2"
    )
    expected_exports = {
        "AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION",
        "AITagShadowExecutionObservationV2",
        "load_ai_tag_shadow_execution_observation_v2",
        "seal_ai_tag_shadow_execution_observation_v2",
    }
    assert expected_exports <= set(provider_receipts.__all__)


def test_v1_outer_invalid_contract_remains_unchanged() -> None:
    payload = _outer_invalid_payload()
    payload["schema_version"] = AI_TAG_SHADOW_EXECUTION_OBSERVATION_SCHEMA_VERSION
    payload.pop("outer_diagnostic_id")

    observation = seal_ai_tag_shadow_execution_observation(payload)

    assert observation.schema_version == "ai-tag-shadow-execution-observation-v1"
    assert not hasattr(observation, "outer_diagnostic_id")

    payload["outer_diagnostic_id"] = _id("deepseek-outer-response-diagnostic", "d")
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        seal_ai_tag_shadow_execution_observation(payload)


def test_v2_outer_invalid_requires_a_diagnostic_and_no_response_artifacts() -> None:
    missing = _outer_invalid_payload()
    missing["outer_diagnostic_id"] = None
    with pytest.raises(ValueError, match="requires an outer diagnostic"):
        seal_ai_tag_shadow_execution_observation_v2(missing)

    wrong_reason = _outer_invalid_payload()
    wrong_reason["reason_code"] = "response_schema_invalid"
    with pytest.raises(ValueError, match="invalid reason"):
        seal_ai_tag_shadow_execution_observation_v2(wrong_reason)

    partial = _outer_invalid_payload()
    partial["provider_response_receipt_id"] = _id("ai-tag-observed-response", "e")
    with pytest.raises(ValueError, match="requires both response artifacts"):
        seal_ai_tag_shadow_execution_observation_v2(partial)

    invalid_diagnostic_id = _outer_invalid_payload()
    invalid_diagnostic_id["outer_diagnostic_id"] = _id("wrong-prefix", "d")
    with pytest.raises(ValueError, match="outer_diagnostic_id"):
        seal_ai_tag_shadow_execution_observation_v2(invalid_diagnostic_id)


@pytest.mark.parametrize(
    "payload_factory",
    [_valid_shape_payload, _inner_invalid_payload],
)
def test_v2_response_observations_require_outer_diagnostic_to_be_empty(
    payload_factory: Callable[[], dict[str, object]],
) -> None:
    payload = payload_factory()
    observation = seal_ai_tag_shadow_execution_observation_v2(payload)
    assert observation.outer_diagnostic_id is None

    payload["outer_diagnostic_id"] = _id("deepseek-outer-response-diagnostic", "d")
    expected = "valid shadow observation" if payload["status"] == "valid_shape" else "inner-invalid"
    with pytest.raises(ValueError, match=expected):
        seal_ai_tag_shadow_execution_observation_v2(payload)


@pytest.mark.parametrize(
    "reason_code",
    [
        "provider_outer_contract_invalid",
        "response_shape_valid",
        "response_schema_invalid",
    ],
)
def test_v2_inner_invalid_rejects_non_validation_reasons(reason_code: str) -> None:
    payload = _inner_invalid_payload()
    payload["reason_code"] = reason_code

    with pytest.raises(ValueError, match="inner-invalid observation uses an invalid reason"):
        seal_ai_tag_shadow_execution_observation_v2(payload)


@pytest.mark.parametrize(
    "status",
    [
        "provider_client_error",
        "provider_rate_limited",
        "provider_server_error",
        "provider_timeout",
        "provider_transport_error",
        "provider_response_too_large",
    ],
)
def test_v2_provider_failures_require_outer_diagnostic_to_be_empty(status: str) -> None:
    payload = _provider_failure_payload(status)
    observation = seal_ai_tag_shadow_execution_observation_v2(payload)
    assert observation.outer_diagnostic_id is None

    payload["outer_diagnostic_id"] = _id("deepseek-outer-response-diagnostic", "d")
    with pytest.raises(ValueError, match="provider failure cannot carry response artifacts"):
        seal_ai_tag_shadow_execution_observation_v2(payload)
