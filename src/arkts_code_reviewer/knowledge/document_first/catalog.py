from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DOCUMENT_CARD_USE_SCOPE,
    DocumentCard,
    MarkdownDocumentMap,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    verify_document_card,
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument

DOCUMENT_CATALOG_BUILD_SCHEMA_VERSION: Literal["document-catalog-build-v1"] = (
    "document-catalog-build-v1"
)
DOCUMENT_CATALOG_BUILDER_VERSION: Literal["document-catalog-builder-v1"] = (
    "document-catalog-builder-v1"
)
DOCUMENT_CATALOG_QUALIFICATION: Literal[
    "navigation_catalog_contract_not_quality_qualified"
] = "navigation_catalog_contract_not_quality_qualified"

_HASH = r"[0-9a-f]{64}"
_CATALOG_ID = rf"^document-catalog:sha256:{_HASH}$"

DocumentCatalogInput = tuple[NormalizedDocument, MarkdownDocumentMap, DocumentCard]


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _source_key(document_map: MarkdownDocumentMap) -> tuple[str, ...]:
    source_ref = document_map.source_ref
    return (
        source_ref.source_id,
        source_ref.revision,
        source_ref.relative_path,
        source_ref.anchor,
        source_ref.authority,
        source_ref.content_hash,
        document_map.document_id,
    )


def _verify_map_card_link(
    document_map: MarkdownDocumentMap,
    card: DocumentCard,
) -> None:
    expected = (
        document_map.document_id,
        document_map.map_id,
        document_map.source_ref,
        document_map.normalized_body_hash,
    )
    actual = (
        card.document_id,
        card.document_map_id,
        card.source_ref,
        card.normalized_body_hash,
    )
    if actual != expected:
        raise ValueError("Document Catalog card does not match its Markdown document map")


class DocumentCatalogEntry(FrozenModel):
    ordinal: Annotated[int, Field(ge=0)]
    document_map: MarkdownDocumentMap
    document_card: DocumentCard

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        document_map = MarkdownDocumentMap.model_validate(
            self.document_map.model_dump(mode="json")
        )
        document_card = DocumentCard.model_validate(
            self.document_card.model_dump(mode="json")
        )
        _verify_map_card_link(document_map, document_card)
        return self


class _DocumentCatalogBuildFields(FrozenModel):
    schema_version: Literal["document-catalog-build-v1"] = (
        DOCUMENT_CATALOG_BUILD_SCHEMA_VERSION
    )
    builder_version: Literal["document-catalog-builder-v1"] = DOCUMENT_CATALOG_BUILDER_VERSION
    entries: Annotated[tuple[DocumentCatalogEntry, ...], Field(min_length=1)]
    document_count: Annotated[int, Field(ge=1)]
    use_scope: Literal["navigation_only_not_evidence"] = DOCUMENT_CARD_USE_SCOPE
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False
    qualification: Literal["navigation_catalog_contract_not_quality_qualified"] = (
        DOCUMENT_CATALOG_QUALIFICATION
    )

    @field_validator("entries", mode="before")
    @classmethod
    def parse_entries(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCatalogBuild.entries")

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        if self.document_count != len(self.entries):
            raise ValueError("DocumentCatalogBuild.document_count does not match entries")
        if tuple(entry.ordinal for entry in self.entries) != tuple(range(len(self.entries))):
            raise ValueError("Document Catalog entry ordinals must be contiguous and ordered")

        source_keys = tuple(_source_key(entry.document_map) for entry in self.entries)
        if source_keys != tuple(sorted(source_keys)):
            raise ValueError("Document Catalog entries must be sorted by source identity")

        document_ids = tuple(entry.document_map.document_id for entry in self.entries)
        source_refs = tuple(key[:-1] for key in source_keys)
        map_ids = tuple(entry.document_map.map_id for entry in self.entries)
        card_ids = tuple(entry.document_card.card_id for entry in self.entries)
        for name, values in (
            ("document_id", document_ids),
            ("source_ref", source_refs),
            ("map_id", map_ids),
            ("card_id", card_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Document Catalog contains duplicate {name}")
        return self


class _DocumentCatalogBuildPayload(_DocumentCatalogBuildFields):
    pass


class DocumentCatalogBuild(_DocumentCatalogBuildFields):
    catalog_id: Annotated[str, Field(pattern=_CATALOG_ID)]

    @model_validator(mode="after")
    def validate_catalog_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"catalog_id"})
        if self.catalog_id != canonical_hash("document-catalog", payload):
            raise ValueError("DocumentCatalogBuild.catalog_id does not match its complete contents")
        return self


def _validated_input(item: DocumentCatalogInput) -> DocumentCatalogInput:
    document, document_map, card = item
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_map = MarkdownDocumentMap.model_validate(document_map.model_dump(mode="json"))
    trusted_card = DocumentCard.model_validate(card.model_dump(mode="json"))
    verify_markdown_document_map(trusted_document, trusted_map)
    verify_document_card(trusted_document, trusted_map, trusted_card)
    return trusted_document, trusted_map, trusted_card


def build_document_catalog(inputs: Iterable[DocumentCatalogInput]) -> DocumentCatalogBuild:
    validated = tuple(_validated_input(item) for item in inputs)
    if not validated:
        raise ValueError("Document Catalog requires at least one trusted document")
    ordered = tuple(sorted(validated, key=lambda item: _source_key(item[1])))
    entries = tuple(
        DocumentCatalogEntry(
            ordinal=ordinal,
            document_map=document_map,
            document_card=card,
        )
        for ordinal, (_, document_map, card) in enumerate(ordered)
    )
    payload = _DocumentCatalogBuildPayload(
        entries=entries,
        document_count=len(entries),
        use_scope=DOCUMENT_CARD_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
        qualification=DOCUMENT_CATALOG_QUALIFICATION,
    ).model_dump(mode="json")
    payload["catalog_id"] = canonical_hash("document-catalog", payload)
    return DocumentCatalogBuild.model_validate(payload)


def verify_document_catalog(
    inputs: Iterable[DocumentCatalogInput],
    catalog: DocumentCatalogBuild,
) -> None:
    rebuilt = build_document_catalog(inputs)
    if rebuilt != catalog:
        raise ValueError("Document Catalog does not match the trusted documents, maps, and cards")


def load_document_catalog(raw: str | bytes) -> DocumentCatalogBuild:
    return load_json_model(raw, DocumentCatalogBuild, "Document Catalog")


__all__ = [
    "DOCUMENT_CATALOG_BUILDER_VERSION",
    "DOCUMENT_CATALOG_BUILD_SCHEMA_VERSION",
    "DOCUMENT_CATALOG_QUALIFICATION",
    "DocumentCatalogBuild",
    "DocumentCatalogEntry",
    "DocumentCatalogInput",
    "build_document_catalog",
    "load_document_catalog",
    "verify_document_catalog",
]
