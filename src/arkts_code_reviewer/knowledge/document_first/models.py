from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
)
from arkts_code_reviewer.knowledge.models import SourceRef, SourceSpan

MARKDOWN_DOCUMENT_MAP_SCHEMA_VERSION: Literal["markdown-document-map-v1"] = (
    "markdown-document-map-v1"
)
DOCUMENT_CARD_DRAFT_SCHEMA_VERSION: Literal["document-card-draft-v1"] = "document-card-draft-v1"
DOCUMENT_CARD_SCHEMA_VERSION: Literal["document-card-v1"] = "document-card-v1"
DOCUMENT_STRUCTURE_BUILDER_VERSION: Literal["markdown-document-structure-v1"] = (
    "markdown-document-structure-v1"
)
DOCUMENT_STRUCTURE_FRONT_MATTER_BUILDER_VERSION: Literal[
    "markdown-document-structure-v2-front-matter"
] = "markdown-document-structure-v2-front-matter"
DOCUMENT_CARD_USE_SCOPE: Literal["navigation_only_not_evidence"] = "navigation_only_not_evidence"

_HASH = r"[0-9a-f]{64}"
_SHA256_RE = re.compile(rf"^sha256:{_HASH}$")
_SECTION_ID_RE = re.compile(rf"^document-section:sha256:{_HASH}$")
_DOCUMENT_MAP_ID_RE = re.compile(rf"^markdown-document-map:sha256:{_HASH}$")
_DOCUMENT_CARD_ID_RE = re.compile(rf"^document-card:sha256:{_HASH}$")

SectionKind = Literal["preamble", "heading", "document_body"]


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


def _validate_identity(model: FrozenModel, field: str, prefix: str, context: str) -> None:
    payload = model.model_dump(mode="json", exclude={field})
    if getattr(model, field) != canonical_hash(prefix, payload):
        raise ValueError(f"{context}.{field} does not match its complete contents")


def _strict_source_span(value: object, context: str) -> object:
    if isinstance(value, SourceSpan):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a SourceSpan object")
    for field in ("start_line", "end_line"):
        if field in value and type(value[field]) is not int:
            raise ValueError(f"{context}.{field} must be an integer")
    return value


def _strict_optional_source_span(value: object, context: str) -> object:
    if value is None:
        return None
    return _strict_source_span(value, context)


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
    content_hash = value.get("content_hash")
    if isinstance(content_hash, str) and not _SHA256_RE.fullmatch(content_hash):
        raise ValueError(f"{context}.content_hash must use canonical sha256:<hex> form")
    return value


class MarkdownSection(FrozenModel):
    section_id: Annotated[str, Field(pattern=_SECTION_ID_RE.pattern)]
    ordinal: Annotated[int, Field(ge=0)]
    kind: SectionKind
    title: Annotated[str, Field(min_length=1, max_length=500)]
    heading_level: Annotated[int | None, Field(ge=1, le=6)] = None
    heading_path: tuple[str, ...]
    parent_section_id: Annotated[str | None, Field(pattern=_SECTION_ID_RE.pattern)] = None
    heading_span: SourceSpan | None = None
    content_span: SourceSpan
    subtree_span: SourceSpan
    content_text_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    subtree_text_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]

    @field_validator("heading_path", mode="before")
    @classmethod
    def parse_heading_path(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "MarkdownSection.heading_path")

    @field_validator("heading_span", mode="before")
    @classmethod
    def parse_heading_span(cls, value: object) -> object:
        return _strict_optional_source_span(value, "MarkdownSection.heading_span")

    @field_validator("content_span", "subtree_span", mode="before")
    @classmethod
    def parse_required_spans(cls, value: object, info: ValidationInfo) -> object:
        return _strict_source_span(value, f"MarkdownSection.{info.field_name}")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "MarkdownSection.title")

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _trimmed_single_line(item, "MarkdownSection.heading_path")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.subtree_span.start_line != self.content_span.start_line:
            raise ValueError("MarkdownSection subtree and content must start together")
        if self.subtree_span.end_line < self.content_span.end_line:
            raise ValueError("MarkdownSection subtree must contain its direct content")
        if self.kind == "heading":
            if self.heading_level is None or self.heading_span is None or not self.heading_path:
                raise ValueError("heading sections require level, heading span, and heading path")
            if self.heading_path[-1] != self.title:
                raise ValueError("heading section title must end its heading path")
            if self.heading_span.start_line != self.content_span.start_line:
                raise ValueError("heading section content must start at its heading")
            if self.heading_span.end_line > self.content_span.end_line:
                raise ValueError("heading span must stay in direct section content")
        else:
            if self.heading_level is not None or self.heading_span is not None:
                raise ValueError("non-heading sections cannot declare heading metadata")
            if self.heading_path or self.parent_section_id is not None:
                raise ValueError("non-heading sections cannot declare heading ancestry")
        return self


class _MarkdownDocumentMapFields(FrozenModel):
    schema_version: Literal["markdown-document-map-v1"] = MARKDOWN_DOCUMENT_MAP_SCHEMA_VERSION
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    title: Annotated[str, Field(min_length=1, max_length=500)]
    language: Annotated[str, Field(min_length=1)]
    release: str | None = None
    api_level: Annotated[int | None, Field(ge=1)] = None
    language_mode: str | None = None
    adapter_version: Annotated[str, Field(min_length=1)]
    normalization_diagnostics: tuple[str, ...] = ()
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    source_line_count: Annotated[int, Field(ge=1)]
    sections: tuple[MarkdownSection, ...]
    builder_version: Literal[
        "markdown-document-structure-v1",
        "markdown-document-structure-v2-front-matter",
    ] = DOCUMENT_STRUCTURE_BUILDER_VERSION
    diagnostics: tuple[str, ...] = ()

    @field_validator("sections", "normalization_diagnostics", "diagnostics", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"MarkdownDocumentMap.{info.field_name}")

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "MarkdownDocumentMap.source_ref")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "MarkdownDocumentMap.title")

    @field_validator("normalization_diagnostics", "diagnostics")
    @classmethod
    def validate_diagnostics(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"MarkdownDocumentMap.{info.field_name}")

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        if not self.sections:
            raise ValueError("MarkdownDocumentMap.sections must not be empty")
        if tuple(section.ordinal for section in self.sections) != tuple(range(len(self.sections))):
            raise ValueError("MarkdownDocumentMap section ordinals must be contiguous and ordered")
        section_ids = tuple(section.section_id for section in self.sections)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("MarkdownDocumentMap section IDs must be unique")

        by_id = {section.section_id: section for section in self.sections}
        previous_start = 0
        previous_content_end = 0
        for section in self.sections:
            if section.content_span.end_line > self.source_line_count:
                raise ValueError("MarkdownDocumentMap content span exceeds source line count")
            if section.subtree_span.end_line > self.source_line_count:
                raise ValueError("MarkdownDocumentMap subtree span exceeds source line count")
            if section.content_span.start_line <= previous_start:
                raise ValueError("MarkdownDocumentMap sections must be ordered by source position")
            if section.content_span.start_line <= previous_content_end:
                raise ValueError("MarkdownDocumentMap direct section content must not overlap")
            previous_start = section.content_span.start_line
            previous_content_end = section.content_span.end_line

            if section.parent_section_id is None:
                if section.kind == "heading" and len(section.heading_path) != 1:
                    raise ValueError("root heading path must contain only its own title")
                continue
            parent = by_id.get(section.parent_section_id)
            if parent is None or parent.ordinal >= section.ordinal:
                raise ValueError("MarkdownDocumentMap parent must be an earlier section")
            if parent.kind != "heading":
                raise ValueError("MarkdownDocumentMap parent must be a heading section")
            if section.heading_path != (*parent.heading_path, section.title):
                raise ValueError("MarkdownDocumentMap heading path must extend its parent path")
            if not (
                parent.subtree_span.start_line < section.subtree_span.start_line
                and parent.subtree_span.end_line >= section.subtree_span.end_line
            ):
                raise ValueError("MarkdownDocumentMap child subtree must be inside its parent")
        return self


class _MarkdownDocumentMapPayload(_MarkdownDocumentMapFields):
    pass


class MarkdownDocumentMap(_MarkdownDocumentMapFields):
    map_id: Annotated[str, Field(pattern=_DOCUMENT_MAP_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_map_id(self) -> Self:
        _validate_identity(self, "map_id", "markdown-document-map", "MarkdownDocumentMap")
        return self


class DocumentSectionSummary(FrozenModel):
    section_id: Annotated[str, Field(pattern=_SECTION_ID_RE.pattern)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _trimmed_single_line(value, "DocumentSectionSummary.summary")


class _DocumentCardContent(FrozenModel):
    document_id: Annotated[str, Field(min_length=1)]
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    primary_topics: tuple[Annotated[str, Field(max_length=200)], ...]
    important_apis: tuple[Annotated[str, Field(max_length=200)], ...]
    section_summaries: tuple[DocumentSectionSummary, ...]

    @field_validator("primary_topics", "important_apis", "section_summaries", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"DocumentCard.{info.field_name}")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _trimmed_single_line(value, "DocumentCard.summary")

    @model_validator(mode="after")
    def validate_unique_sections(self) -> Self:
        section_ids = tuple(item.section_id for item in self.section_summaries)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Document card section summaries must have unique IDs")
        return self


class DocumentCardDraft(_DocumentCardContent):
    schema_version: Literal["document-card-draft-v1"] = DOCUMENT_CARD_DRAFT_SCHEMA_VERSION

    @field_validator("primary_topics", "important_apis")
    @classmethod
    def validate_unique_hints(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _unique_strings(value, f"DocumentCardDraft.{info.field_name}")


class _DocumentCardFields(_DocumentCardContent):
    schema_version: Literal["document-card-v1"] = DOCUMENT_CARD_SCHEMA_VERSION
    document_map_id: Annotated[str, Field(pattern=_DOCUMENT_MAP_ID_RE.pattern)]
    source_ref: SourceRef
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    use_scope: Literal["navigation_only_not_evidence"] = DOCUMENT_CARD_USE_SCOPE
    evidence_eligible: Literal[False] = False

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "DocumentCard.source_ref")

    @field_validator("primary_topics", "important_apis")
    @classmethod
    def validate_sorted_hints(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"DocumentCard.{info.field_name}")


class _DocumentCardPayload(_DocumentCardFields):
    pass


class DocumentCard(_DocumentCardFields):
    card_id: Annotated[str, Field(pattern=_DOCUMENT_CARD_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_card_id(self) -> Self:
        _validate_identity(self, "card_id", "document-card", "DocumentCard")
        return self


def load_document_card_draft(raw: str | bytes) -> DocumentCardDraft:
    return load_json_model(raw, DocumentCardDraft, "document card draft")


def load_markdown_document_map(raw: str | bytes) -> MarkdownDocumentMap:
    return load_json_model(raw, MarkdownDocumentMap, "Markdown document map")


def load_document_card(raw: str | bytes) -> DocumentCard:
    return load_json_model(raw, DocumentCard, "document card")


__all__ = [
    "DOCUMENT_CARD_DRAFT_SCHEMA_VERSION",
    "DOCUMENT_CARD_SCHEMA_VERSION",
    "DOCUMENT_CARD_USE_SCOPE",
    "DOCUMENT_STRUCTURE_BUILDER_VERSION",
    "DOCUMENT_STRUCTURE_FRONT_MATTER_BUILDER_VERSION",
    "MARKDOWN_DOCUMENT_MAP_SCHEMA_VERSION",
    "DocumentCard",
    "DocumentCardDraft",
    "DocumentSectionSummary",
    "MarkdownDocumentMap",
    "MarkdownSection",
    "load_document_card",
    "load_document_card_draft",
    "load_markdown_document_map",
]
