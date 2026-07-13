from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

KNOWLEDGE_SCHEMA_VERSION = "knowledge-v1"
KNOWLEDGE_REVIEW_SCHEMA_VERSION = "knowledge-model-review-v1"

ClauseStatus = Literal["Draft", "Baselined", "Deprecated"]
CurationDecisionValue = Literal["approved", "rejected", "pending"]
ReviewerKind = Literal["human", "model"]
AnnotationOrigin = Literal[
    "source_metadata",
    "deterministic_parser",
    "api_catalog",
    "human_curator",
    "approved_model_enrichment",
]
ExampleKind = Literal["positive", "negative", "neutral"]
ModelReviewDecision = Literal[
    "accept",
    "reject",
    "uncertain",
    "accept_with_corrections",
]
PacketReviewDecision = Literal["accept", "reject", "uncertain"]
AnnotationKind = Literal[
    "api",
    "tag",
    "dimension",
    "domain",
    "keyword",
    "component",
    "decorator",
    "scenario",
]
AnnotationAction = Literal["add", "remove", "replace"]
ApiLanguageMode = Literal["dynamic", "static", "unified"]
AnnotationTargetKind = Literal["clause", "api_symbol"]

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
_REVIEW_ISSUE_CODES = {
    "annotation_error",
    "api_error",
    "applicability_error",
    "boundary_error",
    "conflict",
    "dimension_error",
    "duplicate_clause",
    "example_misclassified",
    "incomplete_semantics",
    "insufficient_evidence",
    "missing_clause",
    "source_mismatch",
    "status_error",
    "tag_error",
    "unsupported_claim",
}
_ANNOTATION_REASON_CODES = {
    "api_not_in_catalog",
    "derived_mapping_mismatch",
    "dimension_not_registered",
    "insufficient_source_evidence",
    "keyword_only_false_positive",
    "source_explicit_metadata",
    "tag_not_applicable",
    "tag_not_registered",
    "version_not_supported",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _stable_identity(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{context} must contain non-empty strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _validate_sha256(value: str, context: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{context} must be a SHA-256 value")
    return value if value.startswith("sha256:") else f"sha256:{value}"


def validate_stable_rule_id(value: str, context: str) -> str:
    """Validate source-derived rule IDs without assuming ASCII-only headings."""
    if not value or value.strip() != value:
        raise ValueError(f"{context} must be non-empty and trimmed")
    if any(character.isspace() and character != " " for character in value):
        raise ValueError(f"{context} must not contain control whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    return value


class SourceSpan(_FrozenModel):
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> SourceSpan:
        if self.end_line < self.start_line:
            raise ValueError("SourceSpan.end_line must be >= start_line")
        return self


class SourceRef(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    revision: str
    relative_path: Annotated[str, Field(min_length=1)]
    anchor: Annotated[str, Field(min_length=1)]
    authority: Annotated[str, Field(min_length=1)]
    content_hash: str

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _REVISION_RE.fullmatch(value):
            raise ValueError("SourceRef.revision must be a 40-character lowercase Git revision")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
            raise ValueError("SourceRef.relative_path must stay below the source root")
        if "\\" in value:
            raise ValueError("SourceRef.relative_path must use POSIX separators")
        return str(path)

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _validate_sha256(value, "SourceRef.content_hash")


class Applicability(_FrozenModel):
    min_api_level: Annotated[int | None, Field(ge=1)] = None
    max_api_level: Annotated[int | None, Field(ge=1)] = None
    releases: tuple[str, ...] = ()
    language_modes: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    system_capabilities: tuple[str, ...] = ()

    @field_validator("releases", "language_modes", "permissions", "system_capabilities")
    @classmethod
    def validate_collections(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _validate_sorted_unique(value, f"Applicability.{info.field_name}")

    @model_validator(mode="after")
    def validate_api_range(self) -> Applicability:
        if (
            self.min_api_level is not None
            and self.max_api_level is not None
            and self.max_api_level < self.min_api_level
        ):
            raise ValueError("Applicability.max_api_level must be >= min_api_level")
        return self


class HeadingNode(_FrozenModel):
    level: Annotated[int, Field(ge=1, le=6)]
    title: Annotated[str, Field(min_length=1)]
    span: SourceSpan


class NormalizedDocument(_FrozenModel):
    schema_version: Literal["knowledge-v1"] = "knowledge-v1"
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    media_type: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    heading_tree: tuple[HeadingNode, ...]
    body: str
    language: Annotated[str, Field(min_length=1)]
    release: str | None = None
    api_level: Annotated[int | None, Field(ge=1)] = None
    language_mode: str | None = None
    adapter_version: Annotated[str, Field(min_length=1)]
    diagnostics: tuple[str, ...] = ()

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, "NormalizedDocument.diagnostics")


class ClauseExample(_FrozenModel):
    kind: ExampleKind
    text: Annotated[str, Field(min_length=1)]
    source_span: SourceSpan


class ClauseCandidate(_FrozenModel):
    schema_version: Literal["knowledge-v1"] = "knowledge-v1"
    candidate_id: Annotated[str, Field(min_length=1)]
    native_rule_id: str | None = None
    rule_type: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]
    heading_path: tuple[str, ...]
    parent_context: str | None = None
    neighbor_candidate_ids: tuple[str, ...] = ()
    applicability: Applicability = Applicability()
    source_ref: SourceRef
    source_span: SourceSpan
    examples: tuple[ClauseExample, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @field_validator("heading_path", "neighbor_candidate_ids", "diagnostics")
    @classmethod
    def validate_sorted_fields(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        if info.field_name == "heading_path":
            if any(not item for item in value):
                raise ValueError("ClauseCandidate.heading_path must contain non-empty strings")
            return value
        return _validate_sorted_unique(value, f"ClauseCandidate.{info.field_name}")

    @field_validator("native_rule_id")
    @classmethod
    def validate_native_rule_id(cls, value: str | None) -> str | None:
        if value is not None and not _RULE_ID_RE.fullmatch(value):
            raise ValueError("ClauseCandidate.native_rule_id has an unsupported format")
        return value

    @model_validator(mode="after")
    def validate_candidate_identity(self) -> ClauseCandidate:
        expected = _stable_identity(
            "clause-candidate",
            {
                "document_id": self.source_ref.source_id,
                "relative_path": self.source_ref.relative_path,
                "native_rule_id": self.native_rule_id,
                "rule_type": self.rule_type,
                "source_span": self.source_span.model_dump(mode="json"),
            },
        )
        if self.candidate_id != expected:
            raise ValueError("ClauseCandidate.candidate_id does not match its source identity")
        return self

    @classmethod
    def create(
        cls,
        *,
        native_rule_id: str | None,
        rule_type: str,
        text: str,
        heading_path: tuple[str, ...],
        parent_context: str | None,
        neighbor_candidate_ids: tuple[str, ...],
        applicability: Applicability,
        source_ref: SourceRef,
        source_span: SourceSpan,
        examples: tuple[ClauseExample, ...] = (),
        diagnostics: tuple[str, ...] = (),
    ) -> ClauseCandidate:
        candidate_id = _stable_identity(
            "clause-candidate",
            {
                "document_id": source_ref.source_id,
                "relative_path": source_ref.relative_path,
                "native_rule_id": native_rule_id,
                "rule_type": rule_type,
                "source_span": source_span.model_dump(mode="json"),
            },
        )
        return cls(
            candidate_id=candidate_id,
            native_rule_id=native_rule_id,
            rule_type=rule_type,
            text=text,
            heading_path=heading_path,
            parent_context=parent_context,
            neighbor_candidate_ids=neighbor_candidate_ids,
            applicability=applicability,
            source_ref=source_ref,
            source_span=source_span,
            examples=examples,
            diagnostics=diagnostics,
        )


class KnowledgeClause(_FrozenModel):
    schema_version: Literal["knowledge-v1"] = "knowledge-v1"
    rule_id: Annotated[str, Field(min_length=1)]
    native_rule_id: str | None = None
    rule_type: Annotated[str, Field(min_length=1)]
    status: ClauseStatus
    authority: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]
    heading_path: tuple[str, ...]
    parent_context: str | None = None
    neighbor_rule_ids: tuple[str, ...] = ()
    applicability: Applicability = Applicability()
    source_ref: SourceRef
    source_span: SourceSpan
    examples: tuple[ClauseExample, ...] = ()
    doc_hash: str
    curation_version: Annotated[str, Field(min_length=1)]
    created_at: datetime
    updated_at: datetime

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        return validate_stable_rule_id(value, "KnowledgeClause.rule_id")

    @field_validator("native_rule_id")
    @classmethod
    def validate_native_rule_id(cls, value: str | None) -> str | None:
        if value is not None and not _RULE_ID_RE.fullmatch(value):
            raise ValueError("KnowledgeClause.native_rule_id has an unsupported format")
        return value

    @field_validator("neighbor_rule_ids")
    @classmethod
    def validate_neighbors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, "KnowledgeClause.neighbor_rule_ids")

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("KnowledgeClause.heading_path must contain non-empty strings")
        return value

    @field_validator("doc_hash")
    @classmethod
    def validate_doc_hash(cls, value: str) -> str:
        return _validate_sha256(value, "KnowledgeClause.doc_hash")

    @model_validator(mode="after")
    def validate_timestamps(self) -> KnowledgeClause:
        if self.updated_at < self.created_at:
            raise ValueError("KnowledgeClause.updated_at must be >= created_at")
        if self.authority != self.source_ref.authority:
            raise ValueError("KnowledgeClause.authority must match SourceRef.authority")
        return self


class ApiAvailability(_FrozenModel):
    language_mode: ApiLanguageMode
    since: Annotated[int | None, Field(ge=1)] = None
    deprecated_since: Annotated[int | None, Field(ge=1)] = None

    @model_validator(mode="after")
    def validate_versions(self) -> ApiAvailability:
        if (
            self.since is not None
            and self.deprecated_since is not None
            and self.deprecated_since < self.since
        ):
            raise ValueError("ApiAvailability.deprecated_since must be >= since")
        return self


class ApiSymbol(_FrozenModel):
    declaration_id: Annotated[str, Field(pattern=r"^api-declaration:sha256:[0-9a-f]{64}$")]
    canonical_name: Annotated[str, Field(min_length=1)]
    aliases: tuple[str, ...] = ()
    module: Annotated[str, Field(min_length=1)]
    kind: Annotated[str, Field(min_length=1)]
    signature: Annotated[str, Field(min_length=1)]
    since: Annotated[int | None, Field(ge=1)] = None
    deprecated_since: Annotated[int | None, Field(ge=1)] = None
    permissions: tuple[str, ...] = ()
    system_capabilities: tuple[str, ...] = ()
    availability: tuple[ApiAvailability, ...] = ()
    source_ref: SourceRef
    source_span: SourceSpan
    catalog_version: Annotated[str, Field(min_length=1)]
    diagnostics: tuple[str, ...] = ()

    @field_validator("aliases", "permissions", "system_capabilities", "diagnostics")
    @classmethod
    def validate_sorted_fields(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _validate_sorted_unique(value, f"ApiSymbol.{info.field_name}")

    @model_validator(mode="after")
    def validate_versions(self) -> ApiSymbol:
        if (
            self.since is not None
            and self.deprecated_since is not None
            and self.deprecated_since < self.since
        ):
            raise ValueError("ApiSymbol.deprecated_since must be >= since")
        modes = [item.language_mode for item in self.availability]
        if modes != sorted(set(modes)):
            raise ValueError("ApiSymbol.availability must be sorted and unique by language_mode")
        expected_id = _stable_identity(
            "api-declaration",
            {
                "canonical_name": self.canonical_name,
                "signature": self.signature,
                "source_id": self.source_ref.source_id,
                "revision": self.source_ref.revision,
                "relative_path": self.source_ref.relative_path,
                "source_span": self.source_span.model_dump(mode="json"),
            },
        )
        if self.declaration_id != expected_id:
            raise ValueError("ApiSymbol.declaration_id does not match its source declaration")
        return self

    @classmethod
    def create(
        cls,
        *,
        canonical_name: str,
        aliases: tuple[str, ...] = (),
        module: str,
        kind: str,
        signature: str,
        since: int | None,
        deprecated_since: int | None,
        permissions: tuple[str, ...] = (),
        system_capabilities: tuple[str, ...] = (),
        availability: tuple[ApiAvailability, ...] = (),
        source_ref: SourceRef,
        source_span: SourceSpan,
        catalog_version: str,
        diagnostics: tuple[str, ...] = (),
    ) -> ApiSymbol:
        declaration_id = _stable_identity(
            "api-declaration",
            {
                "canonical_name": canonical_name,
                "signature": signature,
                "source_id": source_ref.source_id,
                "revision": source_ref.revision,
                "relative_path": source_ref.relative_path,
                "source_span": source_span.model_dump(mode="json"),
            },
        )
        return cls(
            declaration_id=declaration_id,
            canonical_name=canonical_name,
            aliases=aliases,
            module=module,
            kind=kind,
            signature=signature,
            since=since,
            deprecated_since=deprecated_since,
            permissions=permissions,
            system_capabilities=system_capabilities,
            availability=availability,
            source_ref=source_ref,
            source_span=source_span,
            catalog_version=catalog_version,
            diagnostics=diagnostics,
        )


class AnnotationProvenance(_FrozenModel):
    kind: AnnotationKind
    value: Annotated[str, Field(min_length=1)]
    origin: AnnotationOrigin
    evidence_ref: Annotated[str, Field(min_length=1)]


class KnowledgeAnnotation(_FrozenModel):
    target_kind: AnnotationTargetKind
    target_id: Annotated[str, Field(min_length=1)]
    index_version: Annotated[str, Field(min_length=1)]
    func_ids: tuple[str, ...] = ()
    dimension_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    apis: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    raw_keywords: tuple[str, ...] = ()
    llm_keywords: tuple[str, ...] = ()
    scenario: str | None = None
    provenance: tuple[AnnotationProvenance, ...]
    annotation_version: Annotated[str, Field(min_length=1)]

    @field_validator(
        "func_ids",
        "dimension_ids",
        "tags",
        "apis",
        "components",
        "decorators",
        "domains",
        "raw_keywords",
        "llm_keywords",
    )
    @classmethod
    def validate_sorted_fields(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _validate_sorted_unique(value, f"KnowledgeAnnotation.{info.field_name}")

    @model_validator(mode="after")
    def validate_provenance_coverage(self) -> KnowledgeAnnotation:
        expected = {
            *(('dimension', value) for value in self.dimension_ids),
            *(('tag', value) for value in self.tags),
            *(('api', value) for value in self.apis),
            *(('domain', value) for value in self.domains),
            *(('keyword', value) for value in self.raw_keywords),
            *(('keyword', value) for value in self.llm_keywords),
            *(('component', value) for value in self.components),
            *(('decorator', value) for value in self.decorators),
        }
        if self.scenario is not None:
            expected.add(("scenario", self.scenario))
        actual = {(item.kind, item.value) for item in self.provenance}
        if not expected.issubset(actual):
            raise ValueError("KnowledgeAnnotation.provenance must cover every published annotation")
        keys = [(item.kind, item.value, item.origin, item.evidence_ref) for item in self.provenance]
        if keys != sorted(set(keys)):
            raise ValueError("KnowledgeAnnotation.provenance must be sorted and unique")
        return self


class CurationDecision(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    content_hash: str
    content_decision: CurationDecisionValue
    annotation_decision: CurationDecisionValue
    reviewer_kind: ReviewerKind
    reviewer_id: Annotated[str, Field(min_length=1)]
    review_version: Annotated[str, Field(min_length=1)]
    issue_codes: tuple[str, ...] = ()

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _validate_sha256(value, "CurationDecision.content_hash")

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(value, "CurationDecision.issue_codes")


class IndexSegment(_FrozenModel):
    segment_id: Annotated[str, Field(min_length=1)]
    rule_id: Annotated[str, Field(min_length=1)]
    ordinal: Annotated[int, Field(ge=0)]
    retrieval_text: Annotated[str, Field(min_length=1)]
    token_count: Annotated[int, Field(ge=1)]
    segmenter_version: Annotated[str, Field(min_length=1)]
    tokenizer_id: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_segment_id(self) -> IndexSegment:
        if self.segment_id != f"{self.rule_id}#segment-{self.ordinal}":
            raise ValueError("IndexSegment.segment_id must derive from rule_id and ordinal")
        return self


class ModelReviewEvidence(_FrozenModel):
    source_id: Annotated[str, Field(min_length=1)]
    relative_path: Annotated[str, Field(min_length=1)]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    exact_quote: Annotated[str, Field(min_length=1)]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
            raise ValueError("ModelReviewEvidence.relative_path must be a safe relative path")
        if "\\" in value:
            raise ValueError("ModelReviewEvidence.relative_path must use POSIX separators")
        return str(path)

    @model_validator(mode="after")
    def validate_lines(self) -> ModelReviewEvidence:
        if self.end_line < self.start_line:
            raise ValueError("ModelReviewEvidence.end_line must be >= start_line")
        return self


class AnnotationChange(_FrozenModel):
    annotation_kind: AnnotationKind
    current_value: str | None
    proposed_action: AnnotationAction
    proposed_value: str | None
    reason_code: str

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if value not in _ANNOTATION_REASON_CODES:
            raise ValueError("AnnotationChange.reason_code is unsupported")
        return value

    @model_validator(mode="after")
    def validate_action(self) -> AnnotationChange:
        if self.proposed_action == "add" and (
            self.current_value is not None or self.proposed_value is None
        ):
            raise ValueError("add requires proposed_value and no current_value")
        if self.proposed_action == "remove" and (
            self.current_value is None or self.proposed_value is not None
        ):
            raise ValueError("remove requires current_value and no proposed_value")
        if self.proposed_action == "replace" and (
            self.current_value is None or self.proposed_value is None
        ):
            raise ValueError("replace requires current_value and proposed_value")
        return self


class ClauseModelReview(_FrozenModel):
    rule_id: Annotated[str, Field(min_length=1)]
    decision: ModelReviewDecision
    issue_codes: tuple[str, ...]
    evidence: tuple[ModelReviewEvidence, ...]
    annotation_changes: tuple[AnnotationChange, ...]
    rationale: Annotated[str, Field(min_length=1)]

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        value = _validate_sorted_unique(value, "ClauseModelReview.issue_codes")
        if not set(value).issubset(_REVIEW_ISSUE_CODES):
            raise ValueError("ClauseModelReview.issue_codes contains unknown codes")
        return value

    @model_validator(mode="after")
    def validate_decision_evidence(self) -> ClauseModelReview:
        if self.decision == "accept":
            if self.issue_codes or self.evidence or self.annotation_changes:
                raise ValueError("accepted Clause review must not carry issues or changes")
        elif not self.issue_codes or not self.evidence:
            raise ValueError("non-accept Clause review requires issue codes and evidence")
        if self.decision == "accept_with_corrections" and not self.annotation_changes:
            raise ValueError("accept_with_corrections requires annotation changes")
        return self


class MissingClauseReview(_FrozenModel):
    proposed_rule_id: Annotated[str, Field(min_length=1)]
    rule_type: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]
    evidence: Annotated[tuple[ModelReviewEvidence, ...], Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_evidence(self) -> MissingClauseReview:
        if not self.evidence:
            raise ValueError("MissingClauseReview requires evidence")
        return self


class DuplicateClauseGroup(_FrozenModel):
    rule_ids: tuple[str, ...]
    evidence: Annotated[tuple[ModelReviewEvidence, ...], Field(min_length=1)]

    @field_validator("rule_ids")
    @classmethod
    def validate_rule_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        value = _validate_sorted_unique(value, "DuplicateClauseGroup.rule_ids")
        if len(value) < 2:
            raise ValueError("DuplicateClauseGroup requires at least two rule IDs")
        return value


class KnowledgeConflictReview(_FrozenModel):
    conflict_id: Annotated[str, Field(min_length=1)]
    rule_ids: tuple[str, ...]
    evidence: Annotated[tuple[ModelReviewEvidence, ...], Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1)]

    @field_validator("rule_ids")
    @classmethod
    def validate_rule_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        value = _validate_sorted_unique(value, "KnowledgeConflictReview.rule_ids")
        if len(value) < 2:
            raise ValueError("KnowledgeConflictReview requires at least two rule IDs")
        return value


class ModelReviewer(_FrozenModel):
    kind: Literal["model"]
    provider: Literal["xai"]
    model: Annotated[str, Field(min_length=1)]
    prompt_version: Literal["grok-knowledge-auditor-v1"]


class ModelReviewSummary(_FrozenModel):
    accepted: Annotated[int, Field(ge=0)]
    rejected: Annotated[int, Field(ge=0)]
    uncertain: Annotated[int, Field(ge=0)]
    with_corrections: Annotated[int, Field(ge=0)]


class KnowledgeModelReview(_FrozenModel):
    schema_version: Literal["knowledge-model-review-v1"] = "knowledge-model-review-v1"
    packet_id: Annotated[str, Field(pattern=r"^knowledge-review-packet:sha256:[0-9a-f]{64}$")]
    reviewer: ModelReviewer
    packet_decision: PacketReviewDecision
    clause_reviews: tuple[ClauseModelReview, ...]
    missing_clauses: tuple[MissingClauseReview, ...]
    duplicate_groups: tuple[DuplicateClauseGroup, ...]
    conflicts: tuple[KnowledgeConflictReview, ...]
    summary: ModelReviewSummary

    @model_validator(mode="after")
    def validate_review_graph(self) -> KnowledgeModelReview:
        review_ids = [review.rule_id for review in self.clause_reviews]
        if review_ids != sorted(set(review_ids)):
            raise ValueError("KnowledgeModelReview.clause_reviews must be sorted and unique")
        decisions = [review.decision for review in self.clause_reviews]
        expected_summary = ModelReviewSummary(
            accepted=decisions.count("accept"),
            rejected=decisions.count("reject"),
            uncertain=decisions.count("uncertain"),
            with_corrections=decisions.count("accept_with_corrections"),
        )
        if self.summary != expected_summary:
            raise ValueError("KnowledgeModelReview.summary does not match Clause decisions")
        can_accept = (
            self.reviewer.model != "unknown"
            and all(decision == "accept" for decision in decisions)
            and not self.missing_clauses
            and not self.duplicate_groups
            and not self.conflicts
        )
        if (self.packet_decision == "accept") != can_accept:
            raise ValueError("KnowledgeModelReview.packet_decision does not match review findings")
        return self


__all__ = [
    "ApiSymbol",
    "Applicability",
    "AnnotationChange",
    "AnnotationProvenance",
    "ClauseCandidate",
    "ClauseExample",
    "ClauseModelReview",
    "CurationDecision",
    "HeadingNode",
    "IndexSegment",
    "KNOWLEDGE_REVIEW_SCHEMA_VERSION",
    "KNOWLEDGE_SCHEMA_VERSION",
    "KnowledgeAnnotation",
    "KnowledgeClause",
    "KnowledgeModelReview",
    "ModelReviewEvidence",
    "NormalizedDocument",
    "SourceRef",
    "SourceSpan",
]
