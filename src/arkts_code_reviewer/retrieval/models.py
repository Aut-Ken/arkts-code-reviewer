from __future__ import annotations

import hashlib
import json
import math
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
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
)

RETRIEVAL_REQUEST_SCHEMA_VERSION = "retrieval-request-v1"
KNOWLEDGE_INDEX_SCHEMA_VERSION = "knowledge-index-v1"
EVIDENCE_PACK_SCHEMA_VERSION = "evidence-pack-v1"

IndexOrigin = Literal["publication", "golden_fixture"]
ApplicabilityResult = Literal["applicable", "unknown"]
MatchKind = Literal[
    "rule_id",
    "api",
    "component",
    "decorator",
    "tag",
    "keyword",
    "vector",
    "neighbor",
]
MatchScope = Literal["unit_exact", "file_hint", "semantic", "context"]
RetrievalDiagnosticCode = Literal[
    "applicability_unknown",
    "budget_exhausted",
    "context_dispatch_blocked",
    "embedding_unavailable",
    "empty_result",
    "parser_degraded",
    "vector_index_unavailable",
]

_INDEX_VERSION_PATTERN = r"^knowledge-index:sha256:[0-9a-f]{64}$"
_REQUEST_ID_PATTERN = r"^retrieval-request:sha256:[0-9a-f]{64}$"
_EVIDENCE_PACK_ID_PATTERN = r"^evidence-pack:sha256:[0-9a-f]{64}$"
_SOURCE_BUNDLE_ID_PATTERN = r"^source-bundle:sha256:[0-9a-f]{64}$"
_PUBLISHED_BUILD_ID_PATTERN = (
    r"^(?:published-knowledge|retrieval-fixture):sha256:[0-9a-f]{64}$"
)


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


def _canonical_hash(prefix: str, payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(raw).hexdigest()}"


def _parse_sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


class TargetPlatform(_FrozenModel):
    release: str | None = None
    api_level: Annotated[int | None, Field(ge=1)] = None
    language_mode: str | None = None
    permissions: tuple[str, ...] | None = None
    system_capabilities: tuple[str, ...] | None = None

    @field_validator("permissions", "system_capabilities", mode="before")
    @classmethod
    def parse_optional_sequences(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_sequence(value, "TargetPlatform capability values")

    @field_validator("permissions", "system_capabilities")
    @classmethod
    def validate_optional_sequences(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        return None if value is None else _validate_strings(value, "TargetPlatform values")

    @field_validator("release", "language_mode")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("TargetPlatform text must be non-empty and trimmed")
        return value


class UnitExactSignals(_FrozenModel):
    apis: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    syntax: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    resource_references: tuple[str, ...] = ()

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "resource_references",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Unit exact signals")

    @field_validator(
        "apis",
        "components",
        "decorators",
        "attributes",
        "symbols",
        "syntax",
        "calls",
        "resource_references",
    )
    @classmethod
    def validate_sequences(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_strings(value, "Unit exact signals")


class ParserContextQuality(_FrozenModel):
    parser_layer: Literal["L0", "L1", "parse_degraded"]
    context_degraded: bool
    error_nodes: Annotated[int | None, Field(ge=0)] = None
    missing_nodes: Annotated[int | None, Field(ge=0)] = None

    @model_validator(mode="after")
    def validate_node_counts(self) -> ParserContextQuality:
        if (self.error_nodes is None) != (self.missing_nodes is None):
            raise ValueError("Parser node counts must be provided together")
        if self.parser_layer != "L1" and self.error_nodes is not None:
            raise ValueError("Only L1 quality may carry AST node counts")
        return self


class RetrievalUnitRequest(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=r"^code-source:sha256:[0-9a-f]{64}$")]
    profile_id: Annotated[str, Field(pattern=r"^feature-profile:sha256:[0-9a-f]{64}$")]
    review_question_ids: tuple[str, ...]
    dispatchable_review_question_ids: tuple[str, ...]
    exact_signals: UnitExactSignals
    exact_tags: tuple[str, ...]
    routing_tags: tuple[str, ...]
    retrieval_dimension_ids: tuple[str, ...]
    routing_dimension_ids: tuple[str, ...]
    requested_rule_ids: tuple[str, ...] = ()
    semantic_code_excerpt: Annotated[str | None, Field(max_length=2000)] = None
    intent_summary: Annotated[str, Field(min_length=1)]
    quality: ParserContextQuality
    knowledge_token_budget: Annotated[int, Field(ge=1)]

    @field_validator(
        "review_question_ids",
        "dispatchable_review_question_ids",
        "exact_tags",
        "routing_tags",
        "retrieval_dimension_ids",
        "routing_dimension_ids",
        "requested_rule_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Retrieval Unit fields")

    @field_validator(
        "review_question_ids",
        "dispatchable_review_question_ids",
        "exact_tags",
        "routing_tags",
        "retrieval_dimension_ids",
        "routing_dimension_ids",
        "requested_rule_ids",
    )
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_strings(value, "Retrieval Unit fields")

    @field_validator("intent_summary", "semantic_code_excerpt")
    @classmethod
    def validate_semantic_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("semantic query text must be non-empty trimmed single-line text")
        return value

    @model_validator(mode="after")
    def validate_registry_and_dispatch(self) -> RetrievalUnitRequest:
        if not set(self.dispatchable_review_question_ids).issubset(
            self.review_question_ids
        ):
            raise ValueError("dispatchable questions must be bound review questions")
        feature_config = load_default_feature_config()
        if not set((*self.exact_tags, *self.routing_tags)).issubset(
            feature_config.tags_by_id
        ):
            raise ValueError("Retrieval Unit contains unregistered Tags")
        if not set(
            (*self.retrieval_dimension_ids, *self.routing_dimension_ids)
        ).issubset(feature_config.dimensions_by_id):
            raise ValueError("Retrieval Unit contains unregistered Dimensions")
        disabled_dimensions = {
            dimension_id
            for dimension_id in (
                *self.retrieval_dimension_ids,
                *self.routing_dimension_ids,
            )
            if feature_config.dimensions_by_id[dimension_id].retrieval_policy
            == "disabled"
        }
        if disabled_dimensions:
            raise ValueError("Retrieval Unit contains disabled Dimensions")
        if not set(self.review_question_ids).issubset(
            feature_config.review_questions_by_id
        ):
            raise ValueError("Retrieval Unit contains unregistered Review Questions")
        if not set(self.retrieval_dimension_ids).issubset(
            self.routing_dimension_ids
        ):
            raise ValueError("Retrieval Dimensions must be a subset of routing Dimensions")
        return self


class RetrievalRequest(_FrozenModel):
    schema_version: Literal["retrieval-request-v1"] = "retrieval-request-v1"
    request_id: Annotated[str, Field(pattern=_REQUEST_ID_PATTERN)]
    context_plan_id: Annotated[str, Field(pattern=r"^context-plan:sha256:[0-9a-f]{64}$")]
    feature_routing_id: Annotated[
        str,
        Field(pattern=r"^feature-routing:sha256:[0-9a-f]{64}$"),
    ]
    feature_config_version: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    target_platform: TargetPlatform
    total_knowledge_token_budget: Annotated[int, Field(ge=1)]
    units: tuple[RetrievalUnitRequest, ...]

    @field_validator("units", mode="before")
    @classmethod
    def parse_units(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "RetrievalRequest.units")

    def identity_payload(self) -> dict[str, object]:
        return {
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
        units: tuple[RetrievalUnitRequest, ...],
    ) -> Self:
        ordered = tuple(sorted(units, key=lambda item: item.unit_id))
        draft = cls.model_construct(
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
    def validate_request(self) -> RetrievalRequest:
        unit_ids = [item.unit_id for item in self.units]
        if not unit_ids or len(unit_ids) > 50 or unit_ids != sorted(set(unit_ids)):
            raise ValueError("RetrievalRequest requires 1..50 stably sorted Units")
        if sum(item.knowledge_token_budget for item in self.units) != (
            self.total_knowledge_token_budget
        ):
            raise ValueError("Unit knowledge budgets must exhaust the request budget")
        if self.feature_config_version != load_default_feature_config().fingerprint:
            raise ValueError("RetrievalRequest feature config does not match runtime")
        if self.request_id != _canonical_hash(
            "retrieval-request",
            self.identity_payload(),
        ):
            raise ValueError("RetrievalRequest.request_id does not match content")
        return self


class KnowledgeIndexRecord(_FrozenModel):
    clause: KnowledgeClause
    annotation: KnowledgeAnnotation
    domains: tuple[str, ...]
    retrieval_text: Annotated[str, Field(min_length=1)]
    token_count: Annotated[int, Field(ge=1)]
    embedding: tuple[float, ...] | None = None

    @field_validator("domains", mode="before")
    @classmethod
    def parse_domains(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "KnowledgeIndexRecord.domains")

    @field_validator("embedding", mode="before")
    @classmethod
    def parse_embedding(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_sequence(value, "KnowledgeIndexRecord.embedding")

    @model_validator(mode="after")
    def validate_record(self) -> KnowledgeIndexRecord:
        _validate_strings(self.domains, "KnowledgeIndexRecord.domains")
        if not self.domains:
            raise ValueError("Knowledge index record requires a Domain")
        if self.clause.status != "Baselined":
            raise ValueError("Knowledge index accepts only Baselined Clauses")
        if (
            self.annotation.target_kind != "clause"
            or self.annotation.target_id != self.clause.rule_id
        ):
            raise ValueError("Knowledge index annotation target does not match Clause")
        if self.retrieval_text != self.retrieval_text.strip():
            raise ValueError("retrieval_text must be trimmed")
        if self.embedding is not None and (
            not self.embedding
            or any(not math.isfinite(value) for value in self.embedding)
        ):
            raise ValueError("Knowledge index embedding must contain finite values")
        return self


class KnowledgeIndex(_FrozenModel):
    schema_version: Literal["knowledge-index-v1"] = "knowledge-index-v1"
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    origin: IndexOrigin
    published_build_id: Annotated[str, Field(pattern=_PUBLISHED_BUILD_ID_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    feature_config_version: Annotated[
        str,
        Field(pattern=r"^feature-config:sha256:[0-9a-f]{64}$"),
    ]
    annotation_version: Annotated[str, Field(min_length=1)]
    catalog_version: Annotated[str, Field(min_length=1)]
    retrieval_version: Annotated[str, Field(min_length=1)]
    retrieval_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^retrieval-config:sha256:[0-9a-f]{64}$"),
    ]
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dimensions: Annotated[int | None, Field(ge=1)] = None
    api_symbols: tuple[ApiSymbol, ...] = ()
    records: tuple[KnowledgeIndexRecord, ...]

    @field_validator("api_symbols", "records", mode="before")
    @classmethod
    def parse_records(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "KnowledgeIndex.records")

    @field_validator("annotation_version", "catalog_version", "retrieval_version")
    @classmethod
    def validate_versions(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Knowledge index versions must be trimmed text")
        return value

    @field_validator("embedding_model", "embedding_version")
    @classmethod
    def validate_optional_embedding_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Embedding metadata must be non-empty trimmed text")
        return value

    @classmethod
    def create(
        cls,
        *,
        origin: IndexOrigin,
        published_build_id: str,
        source_bundle_id: str,
        feature_config_version: str,
        annotation_version: str,
        catalog_version: str,
        retrieval_version: str,
        retrieval_config_fingerprint: str,
        embedding_model: str | None,
        embedding_version: str | None,
        embedding_dimensions: int | None,
        api_symbols: tuple[ApiSymbol, ...],
        records: tuple[KnowledgeIndexRecord, ...],
    ) -> Self:
        ordered = tuple(sorted(records, key=lambda item: item.clause.rule_id))
        ordered_api_symbols = tuple(
            sorted(
                api_symbols,
                key=lambda item: (
                    item.canonical_name,
                    item.signature,
                    item.declaration_id,
                ),
            )
        )
        draft = cls.model_construct(
            index_version="knowledge-index:sha256:" + "0" * 64,
            origin=origin,
            published_build_id=published_build_id,
            source_bundle_id=source_bundle_id,
            feature_config_version=feature_config_version,
            annotation_version=annotation_version,
            catalog_version=catalog_version,
            retrieval_version=retrieval_version,
            retrieval_config_fingerprint=retrieval_config_fingerprint,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            embedding_dimensions=embedding_dimensions,
            api_symbols=ordered_api_symbols,
            records=ordered,
        )
        index_version = _canonical_hash("knowledge-index", draft.identity_payload())
        rebound = tuple(
            item.model_copy(
                update={
                    "annotation": item.annotation.model_copy(
                        update={"index_version": index_version}
                    )
                }
            )
            for item in ordered
        )
        return cls(
            index_version=index_version,
            origin=origin,
            published_build_id=published_build_id,
            source_bundle_id=source_bundle_id,
            feature_config_version=feature_config_version,
            annotation_version=annotation_version,
            catalog_version=catalog_version,
            retrieval_version=retrieval_version,
            retrieval_config_fingerprint=retrieval_config_fingerprint,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            embedding_dimensions=embedding_dimensions,
            api_symbols=ordered_api_symbols,
            records=rebound,
        )

    def identity_payload(self) -> dict[str, object]:
        records: list[dict[str, object]] = []
        for item in self.records:
            annotation = item.annotation.model_dump(
                mode="json",
                exclude={"index_version"},
            )
            records.append(
                {
                    "clause": item.clause.model_dump(mode="json"),
                    "annotation": annotation,
                    "domains": item.domains,
                    "retrieval_text": item.retrieval_text,
                    "token_count": item.token_count,
                    "embedding": item.embedding,
                }
            )
        return {
            "origin": self.origin,
            "published_build_id": self.published_build_id,
            "source_bundle_id": self.source_bundle_id,
            "feature_config_version": self.feature_config_version,
            "annotation_version": self.annotation_version,
            "catalog_version": self.catalog_version,
            "retrieval_version": self.retrieval_version,
            "retrieval_config_fingerprint": self.retrieval_config_fingerprint,
            "embedding_model": self.embedding_model,
            "embedding_version": self.embedding_version,
            "embedding_dimensions": self.embedding_dimensions,
            "api_symbols": [
                item.model_dump(mode="json") for item in self.api_symbols
            ],
            "records": records,
        }

    @model_validator(mode="after")
    def validate_index(self) -> KnowledgeIndex:
        if (self.origin == "publication") != self.published_build_id.startswith(
            "published-knowledge:"
        ):
            raise ValueError("Knowledge index origin does not match build identity")
        rule_ids = [item.clause.rule_id for item in self.records]
        if not rule_ids or rule_ids != sorted(set(rule_ids)):
            raise ValueError("Knowledge index records must be non-empty and rule-sorted")
        if self.feature_config_version != load_default_feature_config().fingerprint:
            raise ValueError("Knowledge index feature config does not match runtime")
        feature_config = load_default_feature_config()
        api_keys = [
            (item.canonical_name, item.signature, item.declaration_id)
            for item in self.api_symbols
        ]
        if api_keys != sorted(set(api_keys)):
            raise ValueError("Knowledge index API symbols must be sorted and unique")
        declaration_ids = [item.declaration_id for item in self.api_symbols]
        if len(declaration_ids) != len(set(declaration_ids)):
            raise ValueError("Knowledge index API declaration IDs must be unique")
        if any(item.catalog_version != self.catalog_version for item in self.api_symbols):
            raise ValueError("Knowledge index API catalog versions disagree")
        for item in self.records:
            if item.annotation.index_version != self.index_version:
                raise ValueError("Knowledge annotation does not match index version")
            if item.annotation.annotation_version != self.annotation_version:
                raise ValueError("Knowledge annotation version does not match index")
            if not set(item.annotation.tags).issubset(feature_config.tags_by_id):
                raise ValueError("Knowledge index contains unregistered Tags")
            if not set(item.annotation.dimension_ids).issubset(
                feature_config.dimensions_by_id
            ):
                raise ValueError("Knowledge index contains unregistered Dimensions")
        embedding_metadata = (
            self.embedding_model,
            self.embedding_version,
            self.embedding_dimensions,
        )
        has_embedding = all(value is not None for value in embedding_metadata)
        if has_embedding != any(value is not None for value in embedding_metadata):
            raise ValueError("Embedding metadata must be all present or all absent")
        for item in self.records:
            if has_embedding:
                if item.embedding is None or len(item.embedding) != self.embedding_dimensions:
                    raise ValueError("Knowledge index embedding dimensions do not match")
            elif item.embedding is not None:
                raise ValueError("Exact-only index must not contain embeddings")
        expected = _canonical_hash("knowledge-index", self.identity_payload())
        if self.index_version != expected:
            raise ValueError("KnowledgeIndex.index_version does not match content")
        return self


class EvidenceMatch(_FrozenModel):
    kind: MatchKind
    value: Annotated[str, Field(min_length=1)]
    scope: MatchScope


class RankDetail(_FrozenModel):
    exact_rank: Annotated[int | None, Field(ge=1)] = None
    vector_rank: Annotated[int | None, Field(ge=1)] = None
    exact_score: Annotated[int, Field(ge=0)] = 0
    vector_similarity: Annotated[float | None, Field(ge=-1, le=1)] = None
    rrf_score: Annotated[float, Field(ge=0)]
    authority_priority: Annotated[int, Field(ge=0)]
    dimension_overlap: Annotated[int, Field(ge=0)]

    @field_validator("vector_similarity", "rrf_score")
    @classmethod
    def validate_floats(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("rank scores must be finite")
        return value


class EvidenceClause(_FrozenModel):
    rank: Annotated[int, Field(ge=1)]
    rule_id: Annotated[str, Field(min_length=1)]
    rule_type: Annotated[str, Field(min_length=1)]
    status: Literal["Baselined"]
    text: Annotated[str, Field(min_length=1)]
    heading_path: tuple[str, ...]
    parent_context: str | None = None
    dimension_ids: tuple[str, ...]
    tags: tuple[str, ...]
    apis: tuple[str, ...]
    components: tuple[str, ...]
    decorators: tuple[str, ...]
    domains: tuple[str, ...]
    source_ref: SourceRef
    matched_by: tuple[EvidenceMatch, ...]
    applicability: ApplicabilityResult
    score: Annotated[float, Field(ge=0)]
    rank_detail: RankDetail
    token_count: Annotated[int, Field(ge=1)]

    @field_validator(
        "heading_path",
        "dimension_ids",
        "tags",
        "apis",
        "components",
        "decorators",
        "domains",
        "matched_by",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Evidence Clause collections")

    @model_validator(mode="after")
    def validate_evidence(self) -> EvidenceClause:
        for values, context in (
            (self.dimension_ids, "dimension_ids"),
            (self.tags, "tags"),
            (self.apis, "apis"),
            (self.components, "components"),
            (self.decorators, "decorators"),
            (self.domains, "domains"),
        ):
            _validate_strings(values, f"EvidenceClause.{context}")
        match_keys = [(item.kind, item.scope, item.value) for item in self.matched_by]
        if not match_keys or match_keys != sorted(set(match_keys)):
            raise ValueError("Evidence matches must be non-empty, sorted, and unique")
        if not math.isfinite(self.score):
            raise ValueError("Evidence score must be finite")
        if self.score != self.rank_detail.rrf_score:
            raise ValueError("Evidence score must equal rank detail RRF score")
        return self


class RetrievalDiagnostic(_FrozenModel):
    code: RetrievalDiagnosticCode
    unit_id: str | None = None
    rule_id: str | None = None
    detail: Annotated[str, Field(min_length=1)]

    @field_validator("unit_id", "rule_id")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("Diagnostic identities must be trimmed")
        return value

    @field_validator("detail")
    @classmethod
    def validate_detail(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Diagnostic detail must be trimmed single-line text")
        return value


class UnitEvidence(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    profile_id: Annotated[str, Field(pattern=r"^feature-profile:sha256:[0-9a-f]{64}$")]
    requested_dimension_ids: tuple[str, ...]
    routing_dimension_ids: tuple[str, ...]
    covered_dimension_ids: tuple[str, ...]
    uncovered_dimension_ids: tuple[str, ...]
    clauses: tuple[EvidenceClause, ...]
    diagnostics: tuple[RetrievalDiagnostic, ...]

    @field_validator(
        "requested_dimension_ids",
        "routing_dimension_ids",
        "covered_dimension_ids",
        "uncovered_dimension_ids",
        "clauses",
        "diagnostics",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "UnitEvidence collections")

    @model_validator(mode="after")
    def validate_unit(self) -> UnitEvidence:
        for values, context in (
            (self.requested_dimension_ids, "requested_dimension_ids"),
            (self.routing_dimension_ids, "routing_dimension_ids"),
            (self.covered_dimension_ids, "covered_dimension_ids"),
            (self.uncovered_dimension_ids, "uncovered_dimension_ids"),
        ):
            _validate_strings(values, f"UnitEvidence.{context}")
        requested = set(self.requested_dimension_ids)
        if set(self.covered_dimension_ids).intersection(self.uncovered_dimension_ids):
            raise ValueError("covered and uncovered Dimensions must be disjoint")
        if set((*self.covered_dimension_ids, *self.uncovered_dimension_ids)) != requested:
            raise ValueError("Dimension coverage must partition requested Dimensions")
        ranks = [item.rank for item in self.clauses]
        rule_ids = [item.rule_id for item in self.clauses]
        if ranks != list(range(1, len(self.clauses) + 1)):
            raise ValueError("Evidence Clause ranks must be contiguous")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Unit Evidence must not repeat a rule")
        diagnostic_keys = [
            (item.code, item.unit_id or "", item.rule_id or "", item.detail)
            for item in self.diagnostics
        ]
        if diagnostic_keys != sorted(set(diagnostic_keys)):
            raise ValueError("Unit Evidence diagnostics must be sorted and unique")
        if any(item.unit_id not in {None, self.unit_id} for item in self.diagnostics):
            raise ValueError("Unit Evidence diagnostic references another Unit")
        return self


class EvidencePack(_FrozenModel):
    schema_version: Literal["evidence-pack-v1"] = "evidence-pack-v1"
    evidence_pack_id: Annotated[str, Field(pattern=_EVIDENCE_PACK_ID_PATTERN)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID_PATTERN)]
    retrieval_version: Annotated[str, Field(min_length=1)]
    retrieval_config_fingerprint: Annotated[
        str,
        Field(pattern=r"^retrieval-config:sha256:[0-9a-f]{64}$"),
    ]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    embedding_version: str | None = None
    degraded: bool
    units: tuple[UnitEvidence, ...]
    diagnostics: tuple[RetrievalDiagnostic, ...]

    @field_validator("units", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "EvidencePack collections")

    @field_validator("retrieval_version")
    @classmethod
    def validate_retrieval_version(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Evidence Pack retrieval_version must be trimmed text")
        return value

    @field_validator("embedding_version")
    @classmethod
    def validate_embedding_version(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Evidence Pack embedding_version must be trimmed text")
        return value

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        retrieval_version: str,
        retrieval_config_fingerprint: str,
        index_version: str,
        source_bundle_id: str,
        embedding_version: str | None,
        units: tuple[UnitEvidence, ...],
        diagnostics: tuple[RetrievalDiagnostic, ...],
    ) -> Self:
        ordered_units = tuple(sorted(units, key=lambda item: item.unit_id))
        ordered_diagnostics = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    item.code,
                    item.unit_id or "",
                    item.rule_id or "",
                    item.detail,
                ),
            )
        )
        degraded_codes = {
            "context_dispatch_blocked",
            "embedding_unavailable",
            "parser_degraded",
            "vector_index_unavailable",
        }
        degraded = any(
            item.code in degraded_codes
            for item in (
                *ordered_diagnostics,
                *(
                    diagnostic
                    for unit in ordered_units
                    for diagnostic in unit.diagnostics
                ),
            )
        )
        draft = cls.model_construct(
            evidence_pack_id="evidence-pack:sha256:" + "0" * 64,
            request_id=request_id,
            retrieval_version=retrieval_version,
            retrieval_config_fingerprint=retrieval_config_fingerprint,
            index_version=index_version,
            source_bundle_id=source_bundle_id,
            embedding_version=embedding_version,
            degraded=degraded,
            units=ordered_units,
            diagnostics=ordered_diagnostics,
        )
        return cls(
            evidence_pack_id=_canonical_hash("evidence-pack", draft.identity_payload()),
            request_id=request_id,
            retrieval_version=retrieval_version,
            retrieval_config_fingerprint=retrieval_config_fingerprint,
            index_version=index_version,
            source_bundle_id=source_bundle_id,
            embedding_version=embedding_version,
            degraded=degraded,
            units=ordered_units,
            diagnostics=ordered_diagnostics,
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "retrieval_version": self.retrieval_version,
            "retrieval_config_fingerprint": self.retrieval_config_fingerprint,
            "index_version": self.index_version,
            "source_bundle_id": self.source_bundle_id,
            "embedding_version": self.embedding_version,
            "degraded": self.degraded,
            "units": [item.model_dump(mode="json") for item in self.units],
            "diagnostics": [item.model_dump(mode="json") for item in self.diagnostics],
        }

    @model_validator(mode="after")
    def validate_pack(self) -> EvidencePack:
        unit_ids = [item.unit_id for item in self.units]
        if not unit_ids or unit_ids != sorted(set(unit_ids)):
            raise ValueError("Evidence Pack Units must be non-empty, sorted, and unique")
        diagnostic_keys = [
            (item.code, item.unit_id or "", item.rule_id or "", item.detail)
            for item in self.diagnostics
        ]
        if diagnostic_keys != sorted(set(diagnostic_keys)):
            raise ValueError("Evidence Pack diagnostics must be sorted and unique")
        degraded_codes = {
            "context_dispatch_blocked",
            "embedding_unavailable",
            "parser_degraded",
            "vector_index_unavailable",
        }
        expected_degraded = any(
            item.code in degraded_codes
            for item in (*self.diagnostics, *(d for unit in self.units for d in unit.diagnostics))
        )
        if self.degraded != expected_degraded:
            raise ValueError("Evidence Pack degraded flag does not match diagnostics")
        expected = _canonical_hash("evidence-pack", self.identity_payload())
        if self.evidence_pack_id != expected:
            raise ValueError("EvidencePack.evidence_pack_id does not match content")
        return self


def load_knowledge_index(raw: str | bytes) -> KnowledgeIndex:
    return _load_json_model(raw, KnowledgeIndex, "Knowledge index")


def load_retrieval_request(raw: str | bytes) -> RetrievalRequest:
    return _load_json_model(raw, RetrievalRequest, "Retrieval request")


def load_evidence_pack(raw: str | bytes) -> EvidencePack:
    return _load_json_model(raw, EvidencePack, "Evidence Pack")


def _load_json_model[ModelT: BaseModel](
    raw: str | bytes,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{context} must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError(f"{context} input must be str or bytes")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context} JSON: {exc}") from exc
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


__all__ = [
    "EVIDENCE_PACK_SCHEMA_VERSION",
    "KNOWLEDGE_INDEX_SCHEMA_VERSION",
    "RETRIEVAL_REQUEST_SCHEMA_VERSION",
    "ApplicabilityResult",
    "EvidenceClause",
    "EvidenceMatch",
    "EvidencePack",
    "KnowledgeIndex",
    "KnowledgeIndexRecord",
    "ParserContextQuality",
    "RankDetail",
    "RetrievalDiagnostic",
    "RetrievalRequest",
    "RetrievalUnitRequest",
    "TargetPlatform",
    "UnitEvidence",
    "UnitExactSignals",
    "load_evidence_pack",
    "load_knowledge_index",
    "load_retrieval_request",
]
