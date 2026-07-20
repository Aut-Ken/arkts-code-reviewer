from __future__ import annotations

from typing import Any, Literal

import pytest
import test_retrieval_shadow_evaluation_d0 as d0_helpers

from arkts_code_reviewer.knowledge.models import Applicability, SourceRef
from arkts_code_reviewer.retrieval.shadow_models_v3 import RetrievalShadowResultV3
from arkts_code_reviewer.retrieval_validation.document_truth import (
    seal_retrieval_document_truth_v1,
)
from arkts_code_reviewer.retrieval_validation.shadow_evaluation import (
    build_retrieval_shadow_evaluation_v1,
)


def _seal_rehashed_shadow_result(payload: dict[str, Any]) -> RetrievalShadowResultV3:
    payload.pop("result_id")
    payload["result_id"] = d0_helpers._canonical_hash(  # noqa: SLF001
        "retrieval-shadow-result",
        payload,
    )
    return RetrievalShadowResultV3.model_validate(payload)


def test_rehashed_shadow_result_config_must_match_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    truth = d0_helpers._truth(index, authority)  # noqa: SLF001
    payload: dict[str, Any] = result.model_dump(mode="json")
    replacement_digest = "f" * 64
    if payload["base_retrieval_config_fingerprint"].endswith(replacement_digest):
        replacement_digest = "e" * 64
    payload["base_retrieval_config_fingerprint"] = f"retrieval-config:sha256:{replacement_digest}"
    rebound = _seal_rehashed_shadow_result(payload)

    with pytest.raises(
        ValueError,
        match="Shadow Result and KnowledgeIndex Retrieval configs differ",
    ):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, rebound)  # noqa: SLF001
        )


def test_truth_feature_config_must_match_request_and_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth(index, authority).model_dump(mode="json")  # noqa: SLF001
    payload.pop("truth_id")
    replacement_digest = "f" * 64
    if payload["feature_config_version"].endswith(replacement_digest):
        replacement_digest = "e" * 64
    payload["feature_config_version"] = f"feature-config:sha256:{replacement_digest}"
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(ValueError, match="different Feature Config versions"):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, result)  # noqa: SLF001
        )


def test_rehashed_shadow_result_unit_budgets_must_match_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    truth = d0_helpers._truth(index, authority)  # noqa: SLF001
    payload: dict[str, Any] = result.model_dump(mode="json")
    for unit in payload["units"]:
        for arm_name in ("static_vector", "hybrid"):
            unit[arm_name]["token_budget"] += 1
    rebound = _seal_rehashed_shadow_result(payload)

    with pytest.raises(ValueError, match="Unit roots are not bound"):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, rebound)  # noqa: SLF001
        )


def test_missing_required_source_locator_cannot_be_counted_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, _result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    units = payload["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    missing = next(clause for clause in clauses if clause["rule_id"] == "R-MISSING")
    duplicate = {
        **missing,
        "rule_id": "R-MISSING-ALIAS",
        "rule_type": "forged-alias-type",
    }
    first_unit["clauses"] = tuple(
        sorted((*clauses, duplicate), key=lambda clause: str(clause["rule_id"]))
    )

    with pytest.raises(ValueError, match="source locators must be unique"):
        seal_retrieval_document_truth_v1(payload)


def test_truth_schema_rejects_extra_fields_qualification_lifts_and_weak_nested_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, _result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001

    extra = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    extra["unexpected"] = "not-allowed"
    with pytest.raises(ValueError, match="extra_forbidden"):
        seal_retrieval_document_truth_v1(extra)

    qualification = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    qualification["evidence_qualification_status"] = "qualified"
    with pytest.raises(ValueError, match="not_qualified"):
        seal_retrieval_document_truth_v1(qualification)

    weak_level = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    units = weak_level["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    first_clause = dict(clauses[0])
    source_applicability = first_clause["applicability"]
    assert isinstance(source_applicability, Applicability)
    applicability = source_applicability.model_dump(mode="json")
    applicability["min_api_level"] = True
    first_clause["applicability"] = applicability
    first_unit["clauses"] = (first_clause, *clauses[1:])
    with pytest.raises(ValueError, match="API levels must be exact integers"):
        seal_retrieval_document_truth_v1(weak_level)

    weak_source = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    source_units = weak_source["units"]
    assert isinstance(source_units, tuple)
    source_unit = source_units[0]
    assert isinstance(source_unit, dict)
    source_clauses = source_unit["clauses"]
    assert isinstance(source_clauses, tuple)
    source_clause = dict(source_clauses[0])
    source_ref = source_clause["source_ref"]
    assert isinstance(source_ref, SourceRef)
    source_payload = source_ref.model_dump(mode="json")
    source_payload["anchor"] = " forged-anchor "
    source_clause["source_ref"] = source_payload
    source_unit["clauses"] = (source_clause, *source_clauses[1:])
    with pytest.raises(ValueError, match=r"source_ref\.anchor"):
        seal_retrieval_document_truth_v1(weak_source)


@pytest.mark.parametrize("relevance", ("acceptable", "irrelevant", "forbidden"))
def test_only_required_truth_may_be_absent_from_index(
    monkeypatch: pytest.MonkeyPatch,
    relevance: Literal["acceptable", "irrelevant", "forbidden"],
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth_payload(  # noqa: SLF001
        index,
        authority,
        include_missing=False,
    )
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        missing = d0_helpers._missing_truth_clause(index)  # noqa: SLF001
        missing["relevance"] = relevance
        unit["clauses"] = tuple(
            sorted((*clauses, missing), key=lambda clause: str(clause["rule_id"]))
        )
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(
        ValueError,
        match="only required Document Truth may be absent from KnowledgeIndex",
    ):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, result)  # noqa: SLF001
        )


def test_truth_rule_type_must_match_index_rule_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    units = payload["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    first_unit["clauses"] = tuple(
        {**clause, "rule_type": "forged-rule-type"} if clause["rule_id"] == "R-AI" else clause
        for clause in clauses
    )
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(ValueError, match="Truth Clause differs from KnowledgeIndex"):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, result)  # noqa: SLF001
        )


def test_truth_heading_path_must_match_index_heading_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    units = payload["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    first_unit["clauses"] = tuple(
        {**clause, "heading_path": ("Forged heading",)}
        if clause["rule_id"] == "R-AI"
        else clause
        for clause in clauses
    )
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(ValueError, match="Truth Clause differs from KnowledgeIndex"):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, result)  # noqa: SLF001
        )


def test_truth_applicability_must_match_index_when_both_are_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = d0_helpers._runtime(monkeypatch)  # noqa: SLF001
    payload = d0_helpers._truth_payload(index, authority)  # noqa: SLF001
    units = payload["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    first_unit["clauses"] = tuple(
        {
            **clause,
            "applicability": {
                "min_api_level": 1,
                "max_api_level": None,
                "releases": (),
                "language_modes": (),
                "permissions": (),
                "system_capabilities": (),
            },
        }
        if clause["rule_id"] == "R-AI"
        else clause
        for clause in clauses
    )
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(ValueError, match="Truth Clause differs from KnowledgeIndex"):
        build_retrieval_shadow_evaluation_v1(
            d0_helpers._input(truth, index, authority, result)  # noqa: SLF001
        )
