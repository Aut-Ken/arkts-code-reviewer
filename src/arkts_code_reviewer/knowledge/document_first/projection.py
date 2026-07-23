from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    SourceAtom,
    SourceAtomSet,
    slice_source_atom_text,
    verify_source_atom_set,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

DOCUMENT_PROJECTION_MAPPING_DRAFT_SCHEMA_VERSION: Literal[
    "document-projection-mapping-draft-v1"
] = "document-projection-mapping-draft-v1"
DOCUMENT_PROJECTION_MAPPING_SCHEMA_VERSION: Literal["document-projection-mapping-v1"] = (
    "document-projection-mapping-v1"
)
DOCUMENT_PROJECTION_SCHEMA_VERSION: Literal["document-projection-v1"] = (
    "document-projection-v1"
)
DOCUMENT_PROJECTION_MANIFEST_SCHEMA_VERSION: Literal["document-projection-manifest-v1"] = (
    "document-projection-manifest-v1"
)
DOCUMENT_PROJECTION_VERIFICATION_SCHEMA_VERSION: Literal[
    "document-projection-verification-v1"
] = "document-projection-verification-v1"
DOCUMENT_PROJECTION_RECORD_SCHEMA_VERSION: Literal["document-projection-record-v1"] = (
    "document-projection-record-v1"
)
DOCUMENT_PROJECTION_RENDERER_VERSION: Literal["document-projection-renderer-v1"] = (
    "document-projection-renderer-v1"
)
DOCUMENT_PROJECTION_USE_SCOPE: Literal["retrieval_projection_only_not_evidence"] = (
    "retrieval_projection_only_not_evidence"
)
DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION: Literal[
    "mechanically_verified_projection_not_semantically_reviewed"
] = "mechanically_verified_projection_not_semantically_reviewed"

CategoryKind = Literal[
    "overview",
    "applicability",
    "api_and_symbols",
    "component_behavior",
    "constraint",
    "prohibition",
    "exception",
    "numeric_limit",
    "failure_behavior",
    "lifecycle_and_resource",
    "performance",
    "security_and_permission",
    "alternative_and_recommendation",
    "example",
    "diagnostic_and_observability",
]

CATEGORY_KINDS: tuple[CategoryKind, ...] = (
    "overview",
    "applicability",
    "api_and_symbols",
    "component_behavior",
    "constraint",
    "prohibition",
    "exception",
    "numeric_limit",
    "failure_behavior",
    "lifecycle_and_resource",
    "performance",
    "security_and_permission",
    "alternative_and_recommendation",
    "example",
    "diagnostic_and_observability",
)

_CATEGORY_LABELS: dict[CategoryKind, str] = {
    "overview": "概述",
    "applicability": "适用条件",
    "api_and_symbols": "API 与符号",
    "component_behavior": "组件行为",
    "constraint": "约束",
    "prohibition": "禁止事项",
    "exception": "例外条件",
    "numeric_limit": "数值限制",
    "failure_behavior": "失败行为",
    "lifecycle_and_resource": "生命周期与资源",
    "performance": "性能",
    "security_and_permission": "安全与权限",
    "alternative_and_recommendation": "替代方案与建议",
    "example": "示例",
    "diagnostic_and_observability": "诊断与可观测性",
}

_HASH = r"[0-9a-f]{64}"
_SHA256_RE = re.compile(rf"^sha256:{_HASH}$")
_MAP_ID_RE = re.compile(rf"^markdown-document-map:sha256:{_HASH}$")
_ATOM_ID_RE = re.compile(rf"^source-atom:sha256:{_HASH}$")
_ATOM_SET_ID_RE = re.compile(rf"^source-atom-set:sha256:{_HASH}$")
_BINDING_ID_RE = re.compile(rf"^projection-binding:sha256:{_HASH}$")
_MAPPING_ID_RE = re.compile(rf"^document-projection-mapping:sha256:{_HASH}$")
_PROJECTION_ID_RE = re.compile(rf"^document-projection:sha256:{_HASH}$")
_MANIFEST_ID_RE = re.compile(rf"^document-projection-manifest:sha256:{_HASH}$")
_VERIFICATION_ID_RE = re.compile(rf"^document-projection-verification:sha256:{_HASH}$")
_RECORD_ID_RE = re.compile(rf"^document-projection-record:sha256:{_HASH}$")


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _trimmed_single_line(value: str, context: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{context} must be non-empty and trimmed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{context} must be a single line without control characters")
    return value


def _unique_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    for value in values:
        _trimmed_single_line(value, context)
    if len(values) != len(set(values)):
        raise ValueError(f"{context} must not contain duplicates")
    return values


def _sorted_unique_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    _unique_strings(values, context)
    if values != tuple(sorted(values)):
        raise ValueError(f"{context} must be sorted")
    return values


def _strict_source_ref(value: object, context: str) -> object:
    if isinstance(value, SourceRef):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a SourceRef object")
    for field in (
        "source_id",
        "revision",
        "relative_path",
        "anchor",
        "authority",
        "content_hash",
    ):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{context}.{field} must be a string")
    return value


def _validate_identity(model: FrozenModel, field: str, prefix: str, context: str) -> None:
    payload = model.model_dump(mode="json", exclude={field})
    if getattr(model, field) != canonical_hash(prefix, payload):
        raise ValueError(f"{context}.{field} does not match its complete contents")


class ProjectionBindingDraft(FrozenModel):
    category_kind: CategoryKind
    display_title: Annotated[str, Field(min_length=1, max_length=500)]
    subject_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    retrieval_aliases: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...]
    required_context_atom_ids: tuple[
        Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...
    ] = ()

    @field_validator(
        "subject_terms",
        "retrieval_aliases",
        "atom_ids",
        "required_context_atom_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"ProjectionBindingDraft.{info.field_name}")

    @field_validator("display_title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "ProjectionBindingDraft.display_title")

    @field_validator(
        "subject_terms",
        "retrieval_aliases",
        "atom_ids",
        "required_context_atom_ids",
    )
    @classmethod
    def validate_unique_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _unique_strings(value, f"ProjectionBindingDraft.{info.field_name}")

    @model_validator(mode="after")
    def validate_atoms(self) -> Self:
        if not self.atom_ids:
            raise ValueError("ProjectionBindingDraft.atom_ids must not be empty")
        if set(self.atom_ids) & set(self.required_context_atom_ids):
            raise ValueError("binding atoms and required context atoms must be disjoint")
        return self


class DocumentProjectionMappingDraft(FrozenModel):
    schema_version: Literal["document-projection-mapping-draft-v1"] = (
        DOCUMENT_PROJECTION_MAPPING_DRAFT_SCHEMA_VERSION
    )
    document_id: Annotated[str, Field(min_length=1)]
    bindings: tuple[ProjectionBindingDraft, ...]
    unclassified_atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...] = ()

    @field_validator("bindings", "unclassified_atom_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"DocumentProjectionMappingDraft.{info.field_name}")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "DocumentProjectionMappingDraft.document_id")

    @field_validator("unclassified_atom_ids")
    @classmethod
    def validate_unclassified(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_strings(value, "DocumentProjectionMappingDraft.unclassified_atom_ids")


class _ProjectionBindingFields(FrozenModel):
    category_kind: CategoryKind
    display_title: Annotated[str, Field(min_length=1, max_length=500)]
    subject_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    retrieval_aliases: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...]
    required_context_atom_ids: tuple[
        Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...
    ] = ()

    @field_validator(
        "subject_terms",
        "retrieval_aliases",
        "atom_ids",
        "required_context_atom_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"ProjectionBinding.{info.field_name}")

    @field_validator("display_title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "ProjectionBinding.display_title")

    @field_validator(
        "subject_terms",
        "retrieval_aliases",
        "atom_ids",
        "required_context_atom_ids",
    )
    @classmethod
    def validate_sorted_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"ProjectionBinding.{info.field_name}")

    @model_validator(mode="after")
    def validate_atoms(self) -> Self:
        if not self.atom_ids:
            raise ValueError("ProjectionBinding.atom_ids must not be empty")
        if set(self.atom_ids) & set(self.required_context_atom_ids):
            raise ValueError("binding atoms and required context atoms must be disjoint")
        return self


class _ProjectionBindingPayload(_ProjectionBindingFields):
    pass


class ProjectionBinding(_ProjectionBindingFields):
    binding_id: Annotated[str, Field(pattern=_BINDING_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_binding_id(self) -> Self:
        _validate_identity(self, "binding_id", "projection-binding", "ProjectionBinding")
        return self


class _DocumentProjectionMappingFields(FrozenModel):
    schema_version: Literal["document-projection-mapping-v1"] = (
        DOCUMENT_PROJECTION_MAPPING_SCHEMA_VERSION
    )
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    document_map_id: Annotated[str, Field(pattern=_MAP_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    bindings: tuple[ProjectionBinding, ...]
    unclassified_atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...] = ()
    use_scope: Literal["retrieval_projection_only_not_evidence"] = (
        DOCUMENT_PROJECTION_USE_SCOPE
    )
    evidence_eligible: Literal[False] = False

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "DocumentProjectionMapping.source_ref")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "DocumentProjectionMapping.document_id")

    @field_validator("bindings", "unclassified_atom_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"DocumentProjectionMapping.{info.field_name}")

    @field_validator("unclassified_atom_ids")
    @classmethod
    def validate_unclassified(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique_strings(
            value,
            "DocumentProjectionMapping.unclassified_atom_ids",
        )

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        binding_ids = tuple(binding.binding_id for binding in self.bindings)
        if binding_ids != tuple(sorted(binding_ids)):
            raise ValueError("DocumentProjectionMapping.bindings must be sorted by binding_id")
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("DocumentProjectionMapping binding IDs must be unique")
        return self


class _DocumentProjectionMappingPayload(_DocumentProjectionMappingFields):
    pass


class DocumentProjectionMapping(_DocumentProjectionMappingFields):
    mapping_id: Annotated[str, Field(pattern=_MAPPING_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_mapping_id(self) -> Self:
        _validate_identity(
            self,
            "mapping_id",
            "document-projection-mapping",
            "DocumentProjectionMapping",
        )
        return self


class _ProjectionManifestFields(FrozenModel):
    schema_version: Literal["document-projection-manifest-v1"] = (
        DOCUMENT_PROJECTION_MANIFEST_SCHEMA_VERSION
    )
    projection_id: Annotated[str, Field(pattern=_PROJECTION_ID_RE.pattern)]
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    document_map_id: Annotated[str, Field(pattern=_MAP_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    mapping_id: Annotated[str, Field(pattern=_MAPPING_ID_RE.pattern)]
    renderer_version: Literal["document-projection-renderer-v1"] = (
        DOCUMENT_PROJECTION_RENDERER_VERSION
    )
    markdown_sha256: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    ordered_atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...]
    ordered_binding_ids: tuple[Annotated[str, Field(pattern=_BINDING_ID_RE.pattern)], ...]
    unclassified_atom_ids: tuple[Annotated[str, Field(pattern=_ATOM_ID_RE.pattern)], ...]
    atom_count: Annotated[int, Field(ge=1)]
    binding_count: Annotated[int, Field(ge=0)]
    unclassified_count: Annotated[int, Field(ge=0)]
    use_scope: Literal["retrieval_projection_only_not_evidence"] = (
        DOCUMENT_PROJECTION_USE_SCOPE
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "ProjectionManifest.source_ref")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "ProjectionManifest.document_id")

    @field_validator(
        "ordered_atom_ids",
        "ordered_binding_ids",
        "unclassified_atom_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"ProjectionManifest.{info.field_name}")

    @model_validator(mode="after")
    def validate_counts_and_order(self) -> Self:
        _unique_strings(self.ordered_atom_ids, "ProjectionManifest.ordered_atom_ids")
        _sorted_unique_strings(
            self.ordered_binding_ids,
            "ProjectionManifest.ordered_binding_ids",
        )
        _sorted_unique_strings(
            self.unclassified_atom_ids,
            "ProjectionManifest.unclassified_atom_ids",
        )
        if self.atom_count != len(self.ordered_atom_ids):
            raise ValueError("ProjectionManifest.atom_count does not match ordered_atom_ids")
        if self.binding_count != len(self.ordered_binding_ids):
            raise ValueError("ProjectionManifest.binding_count does not match ordered_binding_ids")
        if self.unclassified_count != len(self.unclassified_atom_ids):
            raise ValueError(
                "ProjectionManifest.unclassified_count does not match unclassified_atom_ids"
            )
        if not set(self.unclassified_atom_ids) <= set(self.ordered_atom_ids):
            raise ValueError("ProjectionManifest unclassified atoms must be rendered atoms")
        return self


class _ProjectionManifestPayload(_ProjectionManifestFields):
    pass


class ProjectionManifest(_ProjectionManifestFields):
    manifest_id: Annotated[str, Field(pattern=_MANIFEST_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_manifest_id(self) -> Self:
        _validate_identity(
            self,
            "manifest_id",
            "document-projection-manifest",
            "ProjectionManifest",
        )
        return self


class DocumentProjection(FrozenModel):
    schema_version: Literal["document-projection-v1"] = DOCUMENT_PROJECTION_SCHEMA_VERSION
    projection_id: Annotated[str, Field(pattern=_PROJECTION_ID_RE.pattern)]
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    document_map_id: Annotated[str, Field(pattern=_MAP_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    mapping_id: Annotated[str, Field(pattern=_MAPPING_ID_RE.pattern)]
    renderer_version: Literal["document-projection-renderer-v1"] = (
        DOCUMENT_PROJECTION_RENDERER_VERSION
    )
    markdown: Annotated[str, Field(min_length=1)]
    manifest: ProjectionManifest
    use_scope: Literal["retrieval_projection_only_not_evidence"] = (
        DOCUMENT_PROJECTION_USE_SCOPE
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "DocumentProjection.source_ref")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "DocumentProjection.document_id")

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        expected_id = _projection_id(
            document_id=self.document_id,
            source_ref=self.source_ref,
            document_map_id=self.document_map_id,
            atom_set_id=self.atom_set_id,
            mapping_id=self.mapping_id,
        )
        if self.projection_id != expected_id:
            raise ValueError("DocumentProjection.projection_id does not match its upstream inputs")
        manifest = self.manifest
        expected_manifest_fields = (
            manifest.projection_id == self.projection_id
            and manifest.document_id == self.document_id
            and manifest.source_ref == self.source_ref
            and manifest.document_map_id == self.document_map_id
            and manifest.atom_set_id == self.atom_set_id
            and manifest.mapping_id == self.mapping_id
            and manifest.renderer_version == self.renderer_version
            and manifest.markdown_sha256 == sha256_text(self.markdown)
            and manifest.use_scope == self.use_scope
            and manifest.evidence_eligible is self.evidence_eligible
            and manifest.production_qualified is self.production_qualified
        )
        if not expected_manifest_fields:
            raise ValueError("DocumentProjection manifest does not match the projection")
        return self


class _DocumentProjectionVerificationFields(FrozenModel):
    schema_version: Literal["document-projection-verification-v1"] = (
        DOCUMENT_PROJECTION_VERIFICATION_SCHEMA_VERSION
    )
    projection_id: Annotated[str, Field(pattern=_PROJECTION_ID_RE.pattern)]
    manifest_id: Annotated[str, Field(pattern=_MANIFEST_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    mapping_id: Annotated[str, Field(pattern=_MAPPING_ID_RE.pattern)]
    physical_line_count: Annotated[int, Field(ge=1)]
    covered_line_count: Annotated[int, Field(ge=1)]
    eligible_atom_count: Annotated[int, Field(ge=1)]
    mapped_atom_count: Annotated[int, Field(ge=1)]
    unclassified_atom_count: Annotated[int, Field(ge=0)]
    source_text_mutation_count: Literal[0] = 0
    unknown_atom_reference_count: Literal[0] = 0
    duplicate_binding_count: Literal[0] = 0
    canonical_atom_body_occurrence_min: Literal[1] = 1
    canonical_atom_body_occurrence_max: Literal[1] = 1
    scoring_duplicate_atom_count: Literal[0] = 0
    result: Literal["pass"] = "pass"
    qualification: Literal[
        "mechanically_verified_projection_not_semantically_reviewed"
    ] = DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION
    use_scope: Literal["retrieval_projection_only_not_evidence"] = (
        DOCUMENT_PROJECTION_USE_SCOPE
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.covered_line_count != self.physical_line_count:
            raise ValueError("Document projection physical line coverage must be complete")
        if self.mapped_atom_count != self.eligible_atom_count:
            raise ValueError("Document projection eligible Atom coverage must be complete")
        if self.unclassified_atom_count > self.eligible_atom_count:
            raise ValueError("unclassified Atom count cannot exceed eligible Atom count")
        return self


class _DocumentProjectionVerificationPayload(_DocumentProjectionVerificationFields):
    pass


class DocumentProjectionVerification(_DocumentProjectionVerificationFields):
    verification_id: Annotated[str, Field(pattern=_VERIFICATION_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_verification_id(self) -> Self:
        _validate_identity(
            self,
            "verification_id",
            "document-projection-verification",
            "DocumentProjectionVerification",
        )
        return self


class _DocumentProjectionRecordFields(FrozenModel):
    schema_version: Literal["document-projection-record-v1"] = (
        DOCUMENT_PROJECTION_RECORD_SCHEMA_VERSION
    )
    document: NormalizedDocument
    document_map: MarkdownDocumentMap
    atom_set: SourceAtomSet
    mapping: DocumentProjectionMapping
    projection: DocumentProjection
    verification: DocumentProjectionVerification
    qualification: Literal[
        "mechanically_verified_projection_not_semantically_reviewed"
    ] = DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION
    use_scope: Literal["retrieval_projection_only_not_evidence"] = (
        DOCUMENT_PROJECTION_USE_SCOPE
    )
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False

    @model_validator(mode="after")
    def validate_links(self) -> Self:
        if not (
            self.document.document_id
            == self.document_map.document_id
            == self.atom_set.document_id
            == self.mapping.document_id
            == self.projection.document_id
        ):
            raise ValueError("DocumentProjectionRecord document identities disagree")
        if not (
            self.document.source_ref
            == self.document_map.source_ref
            == self.atom_set.source_ref
            == self.mapping.source_ref
            == self.projection.source_ref
        ):
            raise ValueError("DocumentProjectionRecord source references disagree")
        if self.atom_set.document_map_id != self.document_map.map_id:
            raise ValueError("DocumentProjectionRecord AtomSet does not bind the document map")
        if self.mapping.atom_set_id != self.atom_set.atom_set_id:
            raise ValueError("DocumentProjectionRecord Mapping does not bind the AtomSet")
        if self.projection.mapping_id != self.mapping.mapping_id:
            raise ValueError("DocumentProjectionRecord Projection does not bind the Mapping")
        if self.verification.projection_id != self.projection.projection_id:
            raise ValueError("DocumentProjectionRecord verification does not bind the Projection")
        if self.verification.manifest_id != self.projection.manifest.manifest_id:
            raise ValueError("DocumentProjectionRecord verification does not bind the Manifest")
        return self


class _DocumentProjectionRecordPayload(_DocumentProjectionRecordFields):
    pass


class DocumentProjectionRecord(_DocumentProjectionRecordFields):
    record_id: Annotated[str, Field(pattern=_RECORD_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_record_id(self) -> Self:
        _validate_identity(
            self,
            "record_id",
            "document-projection-record",
            "DocumentProjectionRecord",
        )
        return self


def build_document_projection_mapping(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    draft: DocumentProjectionMappingDraft,
) -> DocumentProjectionMapping:
    verify_markdown_document_map(document, document_map)
    verify_source_atom_set(document, document_map, atom_set)
    draft = DocumentProjectionMappingDraft.model_validate(draft.model_dump(mode="json"))
    if draft.document_id != document.document_id:
        raise ValueError("projection mapping draft document_id does not match the source document")

    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    known_atom_ids = set(atom_ids)
    bindings: list[ProjectionBinding] = []
    classified_atom_ids: set[str] = set()
    for draft_binding in draft.bindings:
        binding_atom_ids = set(draft_binding.atom_ids)
        context_atom_ids = set(draft_binding.required_context_atom_ids)
        unknown = (binding_atom_ids | context_atom_ids) - known_atom_ids
        if unknown:
            raise ValueError("projection binding references an unknown Source Atom")
        classified_atom_ids.update(binding_atom_ids)
        binding_payload = _ProjectionBindingPayload(
            category_kind=draft_binding.category_kind,
            display_title=draft_binding.display_title,
            subject_terms=tuple(sorted(draft_binding.subject_terms)),
            retrieval_aliases=tuple(sorted(draft_binding.retrieval_aliases)),
            atom_ids=tuple(sorted(draft_binding.atom_ids)),
            required_context_atom_ids=tuple(sorted(draft_binding.required_context_atom_ids)),
        ).model_dump(mode="json")
        binding_payload["binding_id"] = canonical_hash("projection-binding", binding_payload)
        bindings.append(ProjectionBinding.model_validate(binding_payload))

    bindings.sort(key=lambda item: item.binding_id)
    binding_ids = tuple(item.binding_id for item in bindings)
    if len(binding_ids) != len(set(binding_ids)):
        raise ValueError("projection mapping contains duplicate canonical bindings")

    unclassified_atom_ids = set(draft.unclassified_atom_ids)
    unknown_unclassified = unclassified_atom_ids - known_atom_ids
    if unknown_unclassified:
        raise ValueError("unclassified_atom_ids contains an unknown Source Atom")
    if classified_atom_ids & unclassified_atom_ids:
        raise ValueError("classified and unclassified Source Atoms must be disjoint")
    if classified_atom_ids | unclassified_atom_ids != known_atom_ids:
        raise ValueError("projection mapping must cover every eligible Source Atom exactly")

    mapping_payload = _DocumentProjectionMappingPayload(
        document_id=document.document_id,
        source_ref=document.source_ref,
        document_map_id=document_map.map_id,
        atom_set_id=atom_set.atom_set_id,
        normalized_body_hash=document_map.normalized_body_hash,
        bindings=tuple(bindings),
        unclassified_atom_ids=tuple(sorted(unclassified_atom_ids)),
        use_scope=DOCUMENT_PROJECTION_USE_SCOPE,
        evidence_eligible=False,
    ).model_dump(mode="json")
    mapping_payload["mapping_id"] = canonical_hash(
        "document-projection-mapping",
        mapping_payload,
    )
    return DocumentProjectionMapping.model_validate(mapping_payload)


def verify_document_projection_mapping(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    mapping: DocumentProjectionMapping,
) -> None:
    draft = DocumentProjectionMappingDraft(
        document_id=mapping.document_id,
        bindings=tuple(
            ProjectionBindingDraft(
                category_kind=binding.category_kind,
                display_title=binding.display_title,
                subject_terms=binding.subject_terms,
                retrieval_aliases=binding.retrieval_aliases,
                atom_ids=binding.atom_ids,
                required_context_atom_ids=binding.required_context_atom_ids,
            )
            for binding in mapping.bindings
        ),
        unclassified_atom_ids=mapping.unclassified_atom_ids,
    )
    rebuilt = build_document_projection_mapping(document, document_map, atom_set, draft)
    if rebuilt != mapping:
        raise ValueError("document projection mapping does not match the trusted source inputs")


def _projection_id(
    *,
    document_id: str,
    source_ref: SourceRef,
    document_map_id: str,
    atom_set_id: str,
    mapping_id: str,
) -> str:
    return canonical_hash(
        "document-projection",
        {
            "schema_version": DOCUMENT_PROJECTION_SCHEMA_VERSION,
            "document_id": document_id,
            "source_ref": source_ref.model_dump(mode="json"),
            "document_map_id": document_map_id,
            "atom_set_id": atom_set_id,
            "mapping_id": mapping_id,
            "renderer_version": DOCUMENT_PROJECTION_RENDERER_VERSION,
            "use_scope": DOCUMENT_PROJECTION_USE_SCOPE,
            "evidence_eligible": False,
            "production_qualified": False,
        },
    )


def _escape_markdown_inline(value: str) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _atom_anchor(atom_id: str) -> str:
    return f"source-atom-{atom_id.rsplit(':', maxsplit=1)[1]}"


def _atom_label(atom: SourceAtom) -> str:
    path = " / ".join(atom.heading_path) if atom.heading_path else "文档正文"
    source_lines = f"L{atom.source_span.start_line}-L{atom.source_span.end_line}"
    return f"Atom {atom.ordinal + 1:04d} · {path} · {source_lines}"


def _render_projection_markdown(
    document: NormalizedDocument,
    atom_set: SourceAtomSet,
    mapping: DocumentProjectionMapping,
    projection_id: str,
) -> str:
    atoms_by_id = {atom.atom_id: atom for atom in atom_set.atoms}
    bindings_by_category: dict[CategoryKind, list[ProjectionBinding]] = {
        category: [] for category in CATEGORY_KINDS
    }
    for binding in mapping.bindings:
        bindings_by_category[binding.category_kind].append(binding)

    lines: list[str] = [
        "---",
        f"schema_version: {DOCUMENT_PROJECTION_SCHEMA_VERSION}",
        f"projection_id: {projection_id}",
        f"source_document_id: {_escape_markdown_inline(document.document_id)}",
        f"source_revision: {document.source_ref.revision}",
        f"use_scope: {DOCUMENT_PROJECTION_USE_SCOPE}",
        "evidence_eligible: false",
        "production_qualified: false",
        "---",
        "",
        f"# {_escape_markdown_inline(document.title)} 检索投影视图",
        "",
        "> 本文档是一级文档的派生检索视图。分类标题和别名不是规范证据；",
        "> 原文与最终引用以固定 revision 的一级文档为准。",
        "",
        "## 检索目录",
        "",
    ]

    for category in CATEGORY_KINDS:
        bindings = bindings_by_category[category]
        if not bindings:
            continue
        lines.extend((f"### {_CATEGORY_LABELS[category]}", ""))
        for binding in bindings:
            lines.append(f"- {_escape_markdown_inline(binding.display_title)}")
            for atom_id in sorted(
                binding.atom_ids,
                key=lambda value: atoms_by_id[value].ordinal,
            ):
                atom = atoms_by_id[atom_id]
                lines.append(
                    "  - "
                    f"[{_escape_markdown_inline(_atom_label(atom))}](#{_atom_anchor(atom_id)})"
                )
            if binding.subject_terms:
                subjects = "、".join(
                    _escape_markdown_inline(item) for item in binding.subject_terms
                )
                lines.append(f"  - 主题：{subjects}")
            if binding.retrieval_aliases:
                aliases = "、".join(
                    _escape_markdown_inline(item) for item in binding.retrieval_aliases
                )
                lines.append(f"  - 检索别名：{aliases}")
        lines.append("")

    if mapping.unclassified_atom_ids:
        lines.extend(("### 未分类兜底", ""))
        for atom_id in sorted(
            mapping.unclassified_atom_ids,
            key=lambda value: atoms_by_id[value].ordinal,
        ):
            atom = atoms_by_id[atom_id]
            lines.append(
                f"- [{_escape_markdown_inline(_atom_label(atom))}](#{_atom_anchor(atom_id)})"
            )
        lines.append("")

    lines.extend(
        (
            "## 原文单元库",
            "",
            "> 以下 Source Atom 按一级文档原始顺序排列；每段正文只出现一次。",
            "",
        )
    )
    markdown = "\n".join(lines)
    for atom in atom_set.atoms:
        source_text = slice_source_atom_text(document, atom)
        heading = _escape_markdown_inline(_atom_label(atom))
        markdown += (
            f'<a id="{_atom_anchor(atom.atom_id)}"></a>\n'
            f"### {heading}\n\n"
            f"<!-- atom_id: {atom.atom_id} -->\n"
            f"<!-- source_lines: {atom.source_span.start_line}-{atom.source_span.end_line} -->\n\n"
            f"{source_text}"
        )
        if not source_text.endswith("\n"):
            markdown += "\n"
        markdown += "\n"
    return markdown


def compile_document_projection(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    mapping: DocumentProjectionMapping,
) -> DocumentProjection:
    verify_document_projection_mapping(document, document_map, atom_set, mapping)
    projection_id = _projection_id(
        document_id=document.document_id,
        source_ref=document.source_ref,
        document_map_id=document_map.map_id,
        atom_set_id=atom_set.atom_set_id,
        mapping_id=mapping.mapping_id,
    )
    markdown = _render_projection_markdown(document, atom_set, mapping, projection_id)
    manifest_payload = _ProjectionManifestPayload(
        projection_id=projection_id,
        document_id=document.document_id,
        source_ref=document.source_ref,
        document_map_id=document_map.map_id,
        atom_set_id=atom_set.atom_set_id,
        mapping_id=mapping.mapping_id,
        renderer_version=DOCUMENT_PROJECTION_RENDERER_VERSION,
        markdown_sha256=sha256_text(markdown),
        ordered_atom_ids=tuple(atom.atom_id for atom in atom_set.atoms),
        ordered_binding_ids=tuple(binding.binding_id for binding in mapping.bindings),
        unclassified_atom_ids=mapping.unclassified_atom_ids,
        atom_count=len(atom_set.atoms),
        binding_count=len(mapping.bindings),
        unclassified_count=len(mapping.unclassified_atom_ids),
        use_scope=DOCUMENT_PROJECTION_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
    ).model_dump(mode="json")
    manifest_payload["manifest_id"] = canonical_hash(
        "document-projection-manifest",
        manifest_payload,
    )
    manifest = ProjectionManifest.model_validate(manifest_payload)
    return DocumentProjection(
        projection_id=projection_id,
        document_id=document.document_id,
        source_ref=document.source_ref,
        document_map_id=document_map.map_id,
        atom_set_id=atom_set.atom_set_id,
        mapping_id=mapping.mapping_id,
        renderer_version=DOCUMENT_PROJECTION_RENDERER_VERSION,
        markdown=markdown,
        manifest=manifest,
        use_scope=DOCUMENT_PROJECTION_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
    )


def verify_document_projection(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    mapping: DocumentProjectionMapping,
    projection: DocumentProjection,
) -> DocumentProjectionVerification:
    rebuilt = compile_document_projection(document, document_map, atom_set, mapping)
    if rebuilt != projection:
        raise ValueError("document projection does not match the trusted source and Mapping")
    verification_payload = _DocumentProjectionVerificationPayload(
        projection_id=projection.projection_id,
        manifest_id=projection.manifest.manifest_id,
        atom_set_id=atom_set.atom_set_id,
        mapping_id=mapping.mapping_id,
        physical_line_count=atom_set.source_line_count,
        covered_line_count=atom_set.source_line_count,
        eligible_atom_count=len(atom_set.atoms),
        mapped_atom_count=len(atom_set.atoms),
        unclassified_atom_count=len(mapping.unclassified_atom_ids),
        source_text_mutation_count=0,
        unknown_atom_reference_count=0,
        duplicate_binding_count=0,
        canonical_atom_body_occurrence_min=1,
        canonical_atom_body_occurrence_max=1,
        scoring_duplicate_atom_count=0,
        result="pass",
        qualification=DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION,
        use_scope=DOCUMENT_PROJECTION_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
    ).model_dump(mode="json")
    verification_payload["verification_id"] = canonical_hash(
        "document-projection-verification",
        verification_payload,
    )
    return DocumentProjectionVerification.model_validate(verification_payload)


def build_document_projection_record(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    mapping: DocumentProjectionMapping,
    projection: DocumentProjection,
) -> DocumentProjectionRecord:
    verification = verify_document_projection(
        document,
        document_map,
        atom_set,
        mapping,
        projection,
    )
    payload = _DocumentProjectionRecordPayload(
        document=document,
        document_map=document_map,
        atom_set=atom_set,
        mapping=mapping,
        projection=projection,
        verification=verification,
        qualification=DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION,
        use_scope=DOCUMENT_PROJECTION_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
    ).model_dump(mode="json")
    payload["record_id"] = canonical_hash("document-projection-record", payload)
    return DocumentProjectionRecord.model_validate(payload)


def verify_document_projection_record(record: DocumentProjectionRecord) -> None:
    record = DocumentProjectionRecord.model_validate(record.model_dump(mode="json"))
    verification = verify_document_projection(
        record.document,
        record.document_map,
        record.atom_set,
        record.mapping,
        record.projection,
    )
    if verification != record.verification:
        raise ValueError("document projection record verification does not rebuild")
    rebuilt = build_document_projection_record(
        record.document,
        record.document_map,
        record.atom_set,
        record.mapping,
        record.projection,
    )
    if rebuilt != record:
        raise ValueError("document projection record does not match its trusted artifacts")


def load_document_projection_mapping_draft(raw: str | bytes) -> DocumentProjectionMappingDraft:
    return load_json_model(
        raw,
        DocumentProjectionMappingDraft,
        "document projection mapping draft",
    )


def load_document_projection_mapping(raw: str | bytes) -> DocumentProjectionMapping:
    return load_json_model(raw, DocumentProjectionMapping, "document projection mapping")


def load_document_projection(raw: str | bytes) -> DocumentProjection:
    return load_json_model(raw, DocumentProjection, "document projection")


def load_projection_manifest(raw: str | bytes) -> ProjectionManifest:
    return load_json_model(raw, ProjectionManifest, "document projection manifest")


def load_document_projection_verification(raw: str | bytes) -> DocumentProjectionVerification:
    return load_json_model(
        raw,
        DocumentProjectionVerification,
        "document projection verification",
    )


def load_document_projection_record(raw: str | bytes) -> DocumentProjectionRecord:
    record = load_json_model(raw, DocumentProjectionRecord, "document projection record")
    verify_document_projection_record(record)
    return record


__all__ = [
    "CATEGORY_KINDS",
    "DOCUMENT_PROJECTION_MAPPING_DRAFT_SCHEMA_VERSION",
    "DOCUMENT_PROJECTION_MAPPING_SCHEMA_VERSION",
    "DOCUMENT_PROJECTION_MANIFEST_SCHEMA_VERSION",
    "DOCUMENT_PROJECTION_MECHANICAL_QUALIFICATION",
    "DOCUMENT_PROJECTION_RECORD_SCHEMA_VERSION",
    "DOCUMENT_PROJECTION_RENDERER_VERSION",
    "DOCUMENT_PROJECTION_SCHEMA_VERSION",
    "DOCUMENT_PROJECTION_USE_SCOPE",
    "DOCUMENT_PROJECTION_VERIFICATION_SCHEMA_VERSION",
    "CategoryKind",
    "DocumentProjection",
    "DocumentProjectionMapping",
    "DocumentProjectionMappingDraft",
    "DocumentProjectionRecord",
    "DocumentProjectionVerification",
    "ProjectionBinding",
    "ProjectionBindingDraft",
    "ProjectionManifest",
    "build_document_projection_mapping",
    "build_document_projection_record",
    "compile_document_projection",
    "load_document_projection",
    "load_document_projection_mapping",
    "load_document_projection_mapping_draft",
    "load_document_projection_record",
    "load_document_projection_verification",
    "load_projection_manifest",
    "verify_document_projection",
    "verify_document_projection_mapping",
    "verify_document_projection_record",
]
