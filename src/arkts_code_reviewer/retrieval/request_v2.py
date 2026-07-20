from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.retrieval.models import ParserContextQuality, TargetPlatform

RETRIEVAL_REQUEST_V2_SCHEMA_VERSION: Literal["retrieval-request-v2"] = "retrieval-request-v2"
VECTOR_QUERY_POLICY_V1: Literal["code-exact-facts-v1"] = "code-exact-facts-v1"

_HASH = r"[0-9a-f]{64}"
_REQUEST_ID_PATTERN = rf"^retrieval-request:sha256:{_HASH}$"
_INDEX_VERSION_PATTERN = rf"^knowledge-index:sha256:{_HASH}$"
_CONTEXT_PLAN_ID_PATTERN = rf"^context-plan:sha256:{_HASH}$"
_FEATURE_ROUTING_ID_PATTERN = rf"^feature-routing:sha256:{_HASH}$"
_FEATURE_CONFIG_ID_PATTERN = rf"^feature-config:sha256:{_HASH}$"
_SOURCE_REF_ID_PATTERN = rf"^code-source:sha256:{_HASH}$"
_PROFILE_ID_PATTERN = rf"^feature-profile:sha256:{_HASH}$"
_HYBRID_ANALYSIS_ID_PATTERN = rf"^hybrid-analysis:sha256:{_HASH}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if any(ord(character) < 32 or ord(character) == 127 for value in values for character in value):
        raise ValueError(f"{context} must not contain control characters")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def candidate_dimension_ids_for_ai_tags(
    ai_inferred_tags: tuple[str, ...],
) -> tuple[str, ...]:
    """Project AI-positive Tags to diagnostic-only candidate Dimensions."""

    if not isinstance(ai_inferred_tags, tuple):
        raise TypeError("AI-inferred Tags must be a tuple")
    if any(not isinstance(tag_id, str) for tag_id in ai_inferred_tags):
        raise ValueError("AI-inferred Tags must contain strings")
    ai_inferred_tags = _validate_strings(
        ai_inferred_tags,
        "AI-inferred Tags",
    )
    feature_config = load_default_feature_config()
    active_tag_ids = {
        tag_id
        for tag_id, definition in feature_config.tags_by_id.items()
        if definition.status == "Active"
    }
    tag_ids = set(ai_inferred_tags)
    if not tag_ids.issubset(active_tag_ids):
        raise ValueError("AI-inferred Tags must be registered and Active")
    return tuple(
        dimension_id
        for dimension_id, definition in feature_config.dimensions_by_id.items()
        if definition.status == "Active"
        and definition.retrieval_policy != "disabled"
        and tag_ids.intersection(definition.triggers.any_tag)
    )


class UnitExactSignalsV2(_FrozenModel):
    apis: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    syntax: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    import_uses: tuple[str, ...] = ()
    resource_references: tuple[str, ...] = ()

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "import_uses",
        "resource_references",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "V2 Unit exact signals")

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "import_uses",
        "resource_references",
    )
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_strings(value, "V2 Unit exact signals")


class RetrievalUnitRequestV2(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID_PATTERN)]
    profile_id: Annotated[str, Field(pattern=_PROFILE_ID_PATTERN)]
    hybrid_analysis_id: Annotated[str, Field(pattern=_HYBRID_ANALYSIS_ID_PATTERN)]
    review_question_ids: tuple[str, ...]
    dispatchable_review_question_ids: tuple[str, ...]
    exact_signals: UnitExactSignalsV2
    exact_tags: tuple[str, ...]
    routing_tags: tuple[str, ...]
    ai_inferred_tags: tuple[str, ...]
    tag_disagreements: tuple[str, ...]
    retrieval_dimension_ids: tuple[str, ...]
    routing_dimension_ids: tuple[str, ...]
    candidate_dimension_ids: tuple[str, ...]
    requested_rule_ids: tuple[str, ...] = ()
    semantic_code_excerpt: Annotated[str | None, Field(max_length=2000)] = None
    intent_summary: Annotated[str, Field(min_length=1)]
    vector_query_policy: Literal["code-exact-facts-v1"]
    quality: ParserContextQuality
    knowledge_token_budget: Annotated[int, Field(ge=1)]

    @field_validator(
        "review_question_ids",
        "dispatchable_review_question_ids",
        "exact_tags",
        "routing_tags",
        "ai_inferred_tags",
        "tag_disagreements",
        "retrieval_dimension_ids",
        "routing_dimension_ids",
        "candidate_dimension_ids",
        "requested_rule_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Retrieval V2 Unit fields")

    @field_validator(
        "review_question_ids",
        "dispatchable_review_question_ids",
        "exact_tags",
        "routing_tags",
        "ai_inferred_tags",
        "tag_disagreements",
        "retrieval_dimension_ids",
        "routing_dimension_ids",
        "candidate_dimension_ids",
        "requested_rule_ids",
    )
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_strings(value, "Retrieval V2 Unit fields")

    @field_validator("unit_id", "intent_summary", "semantic_code_excerpt")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Retrieval V2 text must be non-empty, trimmed, and single-line")
        return value

    @model_validator(mode="after")
    def validate_registry_and_trust_separation(self) -> Self:
        if not set(self.dispatchable_review_question_ids).issubset(self.review_question_ids):
            raise ValueError("dispatchable questions must be bound review questions")
        feature_config = load_default_feature_config()
        all_tags = (
            *self.exact_tags,
            *self.routing_tags,
            *self.ai_inferred_tags,
            *self.tag_disagreements,
        )
        if not set(all_tags).issubset(feature_config.tags_by_id):
            raise ValueError("Retrieval V2 Unit contains unregistered Tags")
        if set(self.ai_inferred_tags).intersection(self.tag_disagreements):
            raise ValueError("AI-positive Tags and disagreements must be disjoint")
        if not set(self.tag_disagreements).issubset(self.exact_tags):
            raise ValueError("Tag disagreements must be static exact Tags")
        formal_dimensions = (
            *self.retrieval_dimension_ids,
            *self.routing_dimension_ids,
        )
        all_dimensions = (*formal_dimensions, *self.candidate_dimension_ids)
        if not set(all_dimensions).issubset(feature_config.dimensions_by_id):
            raise ValueError("Retrieval V2 Unit contains unregistered Dimensions")
        if not set(self.retrieval_dimension_ids).issubset(self.routing_dimension_ids):
            raise ValueError("Retrieval Dimensions must be a subset of routing Dimensions")
        if any(
            feature_config.dimensions_by_id[dimension_id].retrieval_policy == "disabled"
            for dimension_id in all_dimensions
        ):
            raise ValueError("Retrieval V2 Unit contains disabled Dimensions")
        if not set(self.review_question_ids).issubset(feature_config.review_questions_by_id):
            raise ValueError("Retrieval V2 Unit contains unregistered Review Questions")
        expected_candidates = candidate_dimension_ids_for_ai_tags(self.ai_inferred_tags)
        if self.candidate_dimension_ids != expected_candidates:
            raise ValueError("candidate Dimensions must rebuild from AI-positive Tags")
        return self


class RetrievalRequestV2(_FrozenModel):
    schema_version: Literal["retrieval-request-v2"]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID_PATTERN)]
    context_plan_id: Annotated[str, Field(pattern=_CONTEXT_PLAN_ID_PATTERN)]
    feature_routing_id: Annotated[str, Field(pattern=_FEATURE_ROUTING_ID_PATTERN)]
    feature_config_version: Annotated[str, Field(pattern=_FEATURE_CONFIG_ID_PATTERN)]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    target_platform: TargetPlatform
    total_knowledge_token_budget: Annotated[int, Field(ge=1)]
    units: tuple[RetrievalUnitRequestV2, ...]

    @field_validator("units", mode="before")
    @classmethod
    def parse_units(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "RetrievalRequestV2.units")

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "context_plan_id": self.context_plan_id,
            "feature_routing_id": self.feature_routing_id,
            "feature_config_version": self.feature_config_version,
            "index_version": self.index_version,
            "target_platform": self.target_platform.model_dump(mode="json"),
            "total_knowledge_token_budget": self.total_knowledge_token_budget,
            "units": [item.model_dump(mode="json") for item in self.units],
        }

    @classmethod
    def create(
        cls,
        *,
        context_plan_id: str,
        feature_routing_id: str,
        feature_config_version: str,
        index_version: str,
        target_platform: TargetPlatform,
        total_knowledge_token_budget: int,
        units: tuple[RetrievalUnitRequestV2, ...],
    ) -> Self:
        ordered = tuple(sorted(units, key=lambda item: item.unit_id))
        draft = cls.model_construct(
            schema_version=RETRIEVAL_REQUEST_V2_SCHEMA_VERSION,
            request_id="retrieval-request:sha256:" + "0" * 64,
            context_plan_id=context_plan_id,
            feature_routing_id=feature_routing_id,
            feature_config_version=feature_config_version,
            index_version=index_version,
            target_platform=target_platform,
            total_knowledge_token_budget=total_knowledge_token_budget,
            units=ordered,
        )
        return cls(
            schema_version=RETRIEVAL_REQUEST_V2_SCHEMA_VERSION,
            request_id=_canonical_hash("retrieval-request", draft.identity_payload()),
            context_plan_id=context_plan_id,
            feature_routing_id=feature_routing_id,
            feature_config_version=feature_config_version,
            index_version=index_version,
            target_platform=target_platform,
            total_knowledge_token_budget=total_knowledge_token_budget,
            units=ordered,
        )

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        unit_ids = tuple(item.unit_id for item in self.units)
        if not unit_ids or len(unit_ids) > 50 or unit_ids != tuple(sorted(set(unit_ids))):
            raise ValueError("RetrievalRequestV2 requires 1..50 stably sorted Units")
        if sum(item.knowledge_token_budget for item in self.units) != (
            self.total_knowledge_token_budget
        ):
            raise ValueError("V2 Unit knowledge budgets must exhaust the request budget")
        if self.feature_config_version != load_default_feature_config().fingerprint:
            raise ValueError("RetrievalRequestV2 feature config does not match runtime")
        expected = _canonical_hash("retrieval-request", self.identity_payload())
        if self.request_id != expected:
            raise ValueError("RetrievalRequestV2.request_id does not match content")
        return self


def load_retrieval_request_v2(raw: str | bytes) -> RetrievalRequestV2:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Retrieval V2 request must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Retrieval V2 request input must be str or bytes")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid Retrieval V2 request JSON: {exc}") from exc
    try:
        return RetrievalRequestV2.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval V2 request: {exc}") from exc


def render_vector_query_v2(unit: RetrievalUnitRequestV2) -> str | None:
    """Render the V2 code-first vector query without any Tag or Dimension prose."""

    try:
        unit = RetrievalUnitRequestV2.model_validate(unit.model_dump(mode="json"))
    except (AttributeError, ValidationError) as exc:
        raise ValueError(f"invalid Retrieval V2 Unit: {exc}") from exc
    parts: list[str] = []
    if unit.semantic_code_excerpt is not None:
        parts.append(unit.semantic_code_excerpt)
    signals = unit.exact_signals
    for label, values in (
        ("apis", signals.apis),
        ("components", signals.components),
        ("decorators", signals.decorators),
        ("attributes", signals.attributes),
        ("symbols", signals.symbols),
        ("syntax", signals.syntax),
        ("calls", signals.calls),
        ("import uses", signals.import_uses),
        ("resources", signals.resource_references),
    ):
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    return "\n".join(parts) or None


__all__ = [
    "RETRIEVAL_REQUEST_V2_SCHEMA_VERSION",
    "VECTOR_QUERY_POLICY_V1",
    "RetrievalRequestV2",
    "RetrievalUnitRequestV2",
    "UnitExactSignalsV2",
    "candidate_dimension_ids_for_ai_tags",
    "load_retrieval_request_v2",
    "render_vector_query_v2",
]
