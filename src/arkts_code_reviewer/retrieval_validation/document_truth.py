from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import Applicability, SourceRef
from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.models import TargetPlatform

RETRIEVAL_DOCUMENT_TRUTH_SCHEMA_VERSION: Literal["retrieval-document-truth-v1"] = (
    "retrieval-document-truth-v1"
)

DocumentRelevance = Literal["required", "acceptable", "irrelevant", "forbidden"]
DocumentTruthSplit = Literal["development", "calibration"]

_HASH = r"[0-9a-f]{64}"
_TRUTH_ID_PATTERN = rf"^retrieval-document-truth:sha256:{_HASH}$"
_INDEX_VERSION_PATTERN = rf"^knowledge-index:sha256:{_HASH}$"
_FEATURE_CONFIG_VERSION_PATTERN = rf"^feature-config:sha256:{_HASH}$"
_BUILD_ID_PATTERN = (
    rf"^(?:published-knowledge|evaluation-knowledge|retrieval-fixture):sha256:{_HASH}$"
)
_SOURCE_BUNDLE_ID_PATTERN = rf"^source-bundle:sha256:{_HASH}$"
_PROFILE_ID_PATTERN = rf"^feature-profile:sha256:{_HASH}$"
_CODE_SOURCE_ID_PATTERN = rf"^code-source:sha256:{_HASH}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _parse_sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in values
    ):
        raise ValueError(f"{context} must contain non-empty trimmed text")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _validate_trimmed_text(value: str, context: str) -> str:
    if (
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be non-empty trimmed text")
    return value


def _validate_source_ref_input(value: object) -> object:
    if isinstance(value, SourceRef):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Document Truth source_ref must be a SourceRef object")
    expected = {
        "source_id",
        "revision",
        "relative_path",
        "anchor",
        "authority",
        "content_hash",
    }
    if set(value) != expected or any(type(value[item]) is not str for item in expected):
        raise ValueError("Document Truth source_ref must use the exact SourceRef string fields")
    return value


def _validate_applicability_input(value: object) -> object:
    if isinstance(value, Applicability):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Document Truth applicability must be an Applicability object")
    expected = {
        "min_api_level",
        "max_api_level",
        "releases",
        "language_modes",
        "permissions",
        "system_capabilities",
    }
    if set(value) != expected:
        raise ValueError("Document Truth applicability must use the exact Applicability fields")
    for field_name in ("min_api_level", "max_api_level"):
        item = value[field_name]
        if item is not None and type(item) is not int:
            raise ValueError("Document Truth API levels must be exact integers")
    for field_name in (
        "releases",
        "language_modes",
        "permissions",
        "system_capabilities",
    ):
        item = value[field_name]
        if not isinstance(item, list | tuple) or any(type(entry) is not str for entry in item):
            raise ValueError("Document Truth applicability collections must contain exact strings")
    return value


def _clause_locator_key(clause: DocumentTruthClauseV1) -> str:
    source_ref = clause.source_ref
    return json.dumps(
        {
            "source_id": source_ref.source_id,
            "revision": source_ref.revision,
            "relative_path": source_ref.relative_path,
            "anchor": source_ref.anchor,
            "content_hash": source_ref.content_hash,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


class DocumentTruthClauseV1(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    relevance: DocumentRelevance
    source_ref: SourceRef
    heading_path: tuple[str, ...]
    rule_type: Annotated[str, Field(min_length=1)]
    applicability: Applicability

    @field_validator("source_ref", mode="before")
    @classmethod
    def validate_source_ref_input(cls, value: object) -> object:
        return _validate_source_ref_input(value)

    @field_validator("applicability", mode="before")
    @classmethod
    def validate_applicability_input(cls, value: object) -> object:
        return _validate_applicability_input(value)

    @field_validator("heading_path", mode="before")
    @classmethod
    def parse_heading_path(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Document Truth heading path")

    @field_validator("rule_id", "rule_type")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validate_trimmed_text(value, "Document Truth identifier")

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(
            not item
            or item != item.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        ):
            raise ValueError("Document Truth heading path must contain trimmed text")
        return value

    @model_validator(mode="after")
    def validate_nested_metadata(self) -> Self:
        for field_name in ("source_id", "relative_path", "anchor", "authority"):
            _validate_trimmed_text(
                getattr(self.source_ref, field_name),
                f"Document Truth source_ref.{field_name}",
            )
        for field_name in (
            "releases",
            "language_modes",
            "permissions",
            "system_capabilities",
        ):
            _validate_strings(
                getattr(self.applicability, field_name),
                f"Document Truth applicability.{field_name}",
            )
        return self


class DocumentTruthUnitV1(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=_CODE_SOURCE_ID_PATTERN)]
    profile_id: Annotated[str, Field(pattern=_PROFILE_ID_PATTERN)]
    split: DocumentTruthSplit
    critical_dimension_ids: tuple[str, ...]
    clauses: tuple[DocumentTruthClauseV1, ...]

    @field_validator("critical_dimension_ids", "clauses", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Document Truth Unit collections")

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Document Truth Unit ID must be trimmed text")
        return value

    @field_validator("critical_dimension_ids")
    @classmethod
    def validate_dimensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_strings(value, "Document Truth critical Dimensions")
        feature_config = load_default_feature_config()
        if not set(value).issubset(feature_config.dimensions_by_id):
            raise ValueError("Document Truth contains an unregistered critical Dimension")
        if any(
            feature_config.dimensions_by_id[item].retrieval_policy == "disabled" for item in value
        ):
            raise ValueError("Document Truth contains a disabled critical Dimension")
        return value

    @model_validator(mode="after")
    def validate_clauses(self) -> Self:
        rule_ids = tuple(item.rule_id for item in self.clauses)
        if rule_ids != tuple(sorted(set(rule_ids))):
            raise ValueError("Document Truth Clauses must be rule-sorted and unique")
        locator_keys = tuple(_clause_locator_key(item) for item in self.clauses)
        if len(locator_keys) != len(set(locator_keys)):
            raise ValueError(
                "Document Truth Clause source locators must be unique within each Unit"
            )
        return self


class _RetrievalDocumentTruthPayloadV1(_FrozenModel):
    schema_version: Literal["retrieval-document-truth-v1"]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    feature_config_version: Annotated[str, Field(pattern=_FEATURE_CONFIG_VERSION_PATTERN)]
    knowledge_build_id: Annotated[str, Field(pattern=_BUILD_ID_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    target_platform: TargetPlatform
    units: tuple[DocumentTruthUnitV1, ...]
    truth_scope: Literal["development_calibration_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]

    @field_validator("units", mode="before")
    @classmethod
    def parse_units(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Document Truth Units")

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        unit_ids = tuple(item.unit_id for item in self.units)
        if not unit_ids or len(unit_ids) > 50 or unit_ids != tuple(sorted(set(unit_ids))):
            raise ValueError("Document Truth requires 1..50 stably sorted Units")
        for unit in self.units:
            for clause in unit.clauses:
                applicability = evaluate_applicability(
                    clause.applicability,
                    self.target_platform,
                ).decision
                if clause.relevance in {"required", "acceptable"} and applicability == "excluded":
                    raise ValueError(
                        "required or acceptable Document Truth cannot be excluded by target"
                    )
        return self


class RetrievalDocumentTruthV1(_RetrievalDocumentTruthPayloadV1):
    truth_id: Annotated[str, Field(pattern=_TRUTH_ID_PATTERN)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = _canonical_hash(
            "retrieval-document-truth",
            self.model_dump(mode="json", exclude={"truth_id"}),
        )
        if self.truth_id != expected:
            raise ValueError("Retrieval Document Truth ID does not match content")
        return self


def seal_retrieval_document_truth_v1(
    payload: Mapping[str, object],
) -> RetrievalDocumentTruthV1:
    if not isinstance(payload, Mapping):
        raise TypeError("Document Truth payload must be a mapping")
    if "truth_id" in payload:
        raise ValueError("unsealed Document Truth payload cannot contain truth_id")
    try:
        validated = _RetrievalDocumentTruthPayloadV1.model_validate(payload)
        sealed = validated.model_dump(mode="json")
        sealed["truth_id"] = _canonical_hash("retrieval-document-truth", sealed)
        return RetrievalDocumentTruthV1.model_validate(sealed)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval Document Truth payload: {exc}") from exc


def load_retrieval_document_truth_v1(raw: str | bytes) -> RetrievalDocumentTruthV1:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Retrieval Document Truth must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Retrieval Document Truth input must be str or bytes")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, _DuplicateKeyError, ValueError) as exc:
        raise ValueError(f"invalid Retrieval Document Truth JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid Retrieval Document Truth JSON: top-level value must be an object")
    try:
        return RetrievalDocumentTruthV1.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval Document Truth: {exc}") from exc


__all__ = [
    "RETRIEVAL_DOCUMENT_TRUTH_SCHEMA_VERSION",
    "DocumentRelevance",
    "DocumentTruthClauseV1",
    "DocumentTruthSplit",
    "DocumentTruthUnitV1",
    "RetrievalDocumentTruthV1",
    "load_retrieval_document_truth_v1",
    "seal_retrieval_document_truth_v1",
]
