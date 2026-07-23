from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
)
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.projection import (
    CategoryKind,
    DocumentProjectionMapping,
    DocumentProjectionMappingDraft,
    ProjectionBindingDraft,
    build_document_projection_mapping,
    verify_document_projection_mapping,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import SourceAtomSet
from arkts_code_reviewer.knowledge.document_first.source_fragments import (
    SourceFragmentSet,
    verify_source_fragment_set,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

SEMANTIC_FACET_SET_DRAFT_SCHEMA_VERSION: Literal["semantic-facet-set-draft-v1"] = (
    "semantic-facet-set-draft-v1"
)
SEMANTIC_FACET_SET_SCHEMA_VERSION: Literal["semantic-facet-set-v1"] = (
    "semantic-facet-set-v1"
)
SEMANTIC_RELATION_GRAPH_DRAFT_SCHEMA_VERSION: Literal[
    "semantic-relation-graph-draft-v1"
] = "semantic-relation-graph-draft-v1"
SEMANTIC_RELATION_GRAPH_SCHEMA_VERSION: Literal["semantic-relation-graph-v1"] = (
    "semantic-relation-graph-v1"
)
SEMANTIC_CONTEXT_POLICY_VERSION: Literal["semantic-context-v1"] = "semantic-context-v1"
SEMANTIC_FACET_USE_SCOPE: Literal["retrieval_navigation_only_not_evidence"] = (
    "retrieval_navigation_only_not_evidence"
)
SEMANTIC_FACET_QUALIFICATION: Literal[
    "mechanically_verified_facets_not_semantically_reviewed"
] = "mechanically_verified_facets_not_semantically_reviewed"
SEMANTIC_RELATION_QUALIFICATION: Literal[
    "mechanically_verified_relations_not_semantically_reviewed"
] = "mechanically_verified_relations_not_semantically_reviewed"

_HASH = r"[0-9a-f]{64}"
_SHA256_RE = re.compile(rf"^sha256:{_HASH}$")
_MAP_ID_RE = re.compile(rf"^markdown-document-map:sha256:{_HASH}$")
_ATOM_SET_ID_RE = re.compile(rf"^source-atom-set:sha256:{_HASH}$")
_FRAGMENT_ID_RE = re.compile(rf"^source-fragment:sha256:{_HASH}$")
_FRAGMENT_SET_ID_RE = re.compile(rf"^source-fragment-set:sha256:{_HASH}$")
_CONTEXT_ID_RE = re.compile(rf"^semantic-context-signature:sha256:{_HASH}$")
_FACET_ID_RE = re.compile(rf"^semantic-facet:sha256:{_HASH}$")
_FACET_SET_ID_RE = re.compile(rf"^semantic-facet-set:sha256:{_HASH}$")
_RELATION_ID_RE = re.compile(rf"^semantic-facet-relation:sha256:{_HASH}$")
_RELATION_GRAPH_ID_RE = re.compile(rf"^semantic-relation-graph:sha256:{_HASH}$")

SemanticRelationKind = Literal[
    "supplements",
    "exception_of",
    "prerequisite_for",
    "example_of",
    "alternative_to",
    "contrasts_with",
    "apparent_conflict",
    "same_subject_different_context",
]

SEMANTIC_RELATION_KINDS: tuple[SemanticRelationKind, ...] = (
    "supplements",
    "exception_of",
    "prerequisite_for",
    "example_of",
    "alternative_to",
    "contrasts_with",
    "apparent_conflict",
    "same_subject_different_context",
)

_SYMMETRIC_RELATION_KINDS = frozenset(
    {
        "alternative_to",
        "contrasts_with",
        "apparent_conflict",
        "same_subject_different_context",
    }
)


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


def _require_explicit_fields(
    model: FrozenModel,
    expected_fields: frozenset[str],
    context: str,
) -> None:
    missing = expected_fields - model.model_fields_set
    if missing:
        raise ValueError(
            f"{context} must explicitly provide every contract field; missing "
            f"{', '.join(sorted(missing))}"
        )


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


class SemanticContextSignatureDraft(FrozenModel):
    primary_fragment_ids: tuple[Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...]
    required_context_fragment_ids: tuple[
        Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...
    ] = ()
    subject_terms: tuple[Annotated[str, Field(max_length=200)], ...]
    component_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    role_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    scenario_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    operation_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    condition_terms: tuple[Annotated[str, Field(max_length=500)], ...] = ()
    version_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()

    @field_validator(
        "primary_fragment_ids",
        "required_context_fragment_ids",
        "subject_terms",
        "component_terms",
        "role_terms",
        "scenario_terms",
        "operation_terms",
        "condition_terms",
        "version_terms",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticContextSignatureDraft.{info.field_name}")

    @field_validator(
        "primary_fragment_ids",
        "required_context_fragment_ids",
        "subject_terms",
        "component_terms",
        "role_terms",
        "scenario_terms",
        "operation_terms",
        "condition_terms",
        "version_terms",
    )
    @classmethod
    def validate_unique_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _unique_strings(value, f"SemanticContextSignatureDraft.{info.field_name}")

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if not self.primary_fragment_ids:
            raise ValueError("Semantic Context requires at least one primary Fragment")
        if not self.subject_terms:
            raise ValueError("Semantic Context requires at least one subject term")
        if set(self.primary_fragment_ids) & set(self.required_context_fragment_ids):
            raise ValueError("Semantic Context primary and required context must be disjoint")
        return self


class _SemanticContextSignatureFields(FrozenModel):
    context_policy_version: Literal["semantic-context-v1"] = SEMANTIC_CONTEXT_POLICY_VERSION
    primary_fragment_ids: tuple[Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...]
    required_context_fragment_ids: tuple[
        Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...
    ] = ()
    subject_terms: tuple[Annotated[str, Field(max_length=200)], ...]
    component_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    role_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    scenario_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    operation_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    condition_terms: tuple[Annotated[str, Field(max_length=500)], ...] = ()
    version_terms: tuple[Annotated[str, Field(max_length=200)], ...] = ()

    @field_validator(
        "primary_fragment_ids",
        "required_context_fragment_ids",
        "subject_terms",
        "component_terms",
        "role_terms",
        "scenario_terms",
        "operation_terms",
        "condition_terms",
        "version_terms",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticContextSignature.{info.field_name}")

    @field_validator("primary_fragment_ids", "required_context_fragment_ids")
    @classmethod
    def validate_fragment_ids(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _unique_strings(value, f"SemanticContextSignature.{info.field_name}")

    @field_validator(
        "subject_terms",
        "component_terms",
        "role_terms",
        "scenario_terms",
        "operation_terms",
        "condition_terms",
        "version_terms",
    )
    @classmethod
    def validate_terms(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"SemanticContextSignature.{info.field_name}")

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if not self.primary_fragment_ids:
            raise ValueError("Semantic Context requires at least one primary Fragment")
        if not self.subject_terms:
            raise ValueError("Semantic Context requires at least one subject term")
        if set(self.primary_fragment_ids) & set(self.required_context_fragment_ids):
            raise ValueError("Semantic Context primary and required context must be disjoint")
        return self


class _SemanticContextSignaturePayload(_SemanticContextSignatureFields):
    pass


class SemanticContextSignature(_SemanticContextSignatureFields):
    context_signature_id: Annotated[str, Field(pattern=_CONTEXT_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_context_signature_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"context_signature_id"})
        if self.context_signature_id != canonical_hash("semantic-context-signature", payload):
            raise ValueError(
                "SemanticContextSignature.context_signature_id does not match its contents"
            )
        return self


class SemanticFacetDraft(FrozenModel):
    display_title: Annotated[str, Field(min_length=1, max_length=500)]
    category_kinds: tuple[CategoryKind, ...]
    retrieval_aliases: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    context: SemanticContextSignatureDraft

    @field_validator("category_kinds", "retrieval_aliases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticFacetDraft.{info.field_name}")

    @field_validator("display_title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticFacetDraft.display_title")

    @field_validator("category_kinds", "retrieval_aliases")
    @classmethod
    def validate_unique_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _unique_strings(value, f"SemanticFacetDraft.{info.field_name}")

    @model_validator(mode="after")
    def validate_categories(self) -> Self:
        if not self.category_kinds:
            raise ValueError("SemanticFacetDraft requires at least one category kind")
        return self


class _SemanticFacetFields(FrozenModel):
    display_title: Annotated[str, Field(min_length=1, max_length=500)]
    category_kinds: tuple[CategoryKind, ...]
    retrieval_aliases: tuple[Annotated[str, Field(max_length=200)], ...] = ()
    context: SemanticContextSignature

    @field_validator("category_kinds", "retrieval_aliases", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticFacet.{info.field_name}")

    @field_validator("display_title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticFacet.display_title")

    @field_validator("category_kinds", "retrieval_aliases")
    @classmethod
    def validate_sorted_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _sorted_unique_strings(value, f"SemanticFacet.{info.field_name}")

    @model_validator(mode="after")
    def validate_categories(self) -> Self:
        if not self.category_kinds:
            raise ValueError("SemanticFacet requires at least one category kind")
        return self


class _SemanticFacetPayload(_SemanticFacetFields):
    pass


class SemanticFacet(_SemanticFacetFields):
    facet_id: Annotated[str, Field(pattern=_FACET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_facet_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"facet_id"})
        if self.facet_id != canonical_hash("semantic-facet", payload):
            raise ValueError("SemanticFacet.facet_id does not match its complete contents")
        return self


class SemanticFacetSetDraft(FrozenModel):
    schema_version: Literal["semantic-facet-set-draft-v1"] = (
        SEMANTIC_FACET_SET_DRAFT_SCHEMA_VERSION
    )
    document_id: Annotated[str, Field(min_length=1)]
    facets: tuple[SemanticFacetDraft, ...]
    unclassified_fragment_ids: tuple[
        Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...
    ] = ()

    @field_validator("facets", "unclassified_fragment_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticFacetSetDraft.{info.field_name}")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticFacetSetDraft.document_id")

    @field_validator("unclassified_fragment_ids")
    @classmethod
    def validate_unclassified(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_strings(value, "SemanticFacetSetDraft.unclassified_fragment_ids")


class _SemanticFacetSetFields(FrozenModel):
    schema_version: Literal["semantic-facet-set-v1"] = SEMANTIC_FACET_SET_SCHEMA_VERSION
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    document_map_id: Annotated[str, Field(pattern=_MAP_ID_RE.pattern)]
    atom_set_id: Annotated[str, Field(pattern=_ATOM_SET_ID_RE.pattern)]
    fragment_set_id: Annotated[str, Field(pattern=_FRAGMENT_SET_ID_RE.pattern)]
    context_policy_version: Literal["semantic-context-v1"] = SEMANTIC_CONTEXT_POLICY_VERSION
    facets: tuple[SemanticFacet, ...]
    unclassified_fragment_ids: tuple[
        Annotated[str, Field(pattern=_FRAGMENT_ID_RE.pattern)], ...
    ] = ()
    use_scope: Literal["retrieval_navigation_only_not_evidence"] = SEMANTIC_FACET_USE_SCOPE
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False
    qualification: Literal[
        "mechanically_verified_facets_not_semantically_reviewed"
    ] = SEMANTIC_FACET_QUALIFICATION

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "SemanticFacetSet.source_ref")

    @field_validator("facets", "unclassified_fragment_ids", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"SemanticFacetSet.{info.field_name}")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticFacetSet.document_id")

    @field_validator("unclassified_fragment_ids")
    @classmethod
    def validate_unclassified(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_strings(value, "SemanticFacetSet.unclassified_fragment_ids")

    @model_validator(mode="after")
    def validate_facets(self) -> Self:
        facet_ids = tuple(facet.facet_id for facet in self.facets)
        if facet_ids != tuple(sorted(facet_ids)):
            raise ValueError("SemanticFacetSet.facets must be sorted by facet_id")
        if len(facet_ids) != len(set(facet_ids)):
            raise ValueError("SemanticFacetSet facet IDs must be unique")
        return self


class _SemanticFacetSetPayload(_SemanticFacetSetFields):
    pass


class SemanticFacetSet(_SemanticFacetSetFields):
    facet_set_id: Annotated[str, Field(pattern=_FACET_SET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_facet_set_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"facet_set_id"})
        if self.facet_set_id != canonical_hash("semantic-facet-set", payload):
            raise ValueError("SemanticFacetSet.facet_set_id does not match its complete contents")
        return self


def _fragment_order(fragment_set: SourceFragmentSet) -> dict[str, int]:
    return {fragment.fragment_id: fragment.ordinal for fragment in fragment_set.fragments}


def _build_context_signature(
    draft: SemanticContextSignatureDraft,
    order: dict[str, int],
) -> SemanticContextSignature:
    payload = _SemanticContextSignaturePayload(
        primary_fragment_ids=tuple(sorted(draft.primary_fragment_ids, key=order.__getitem__)),
        required_context_fragment_ids=tuple(
            sorted(draft.required_context_fragment_ids, key=order.__getitem__)
        ),
        subject_terms=tuple(sorted(draft.subject_terms)),
        component_terms=tuple(sorted(draft.component_terms)),
        role_terms=tuple(sorted(draft.role_terms)),
        scenario_terms=tuple(sorted(draft.scenario_terms)),
        operation_terms=tuple(sorted(draft.operation_terms)),
        condition_terms=tuple(sorted(draft.condition_terms)),
        version_terms=tuple(sorted(draft.version_terms)),
    ).model_dump(mode="json")
    payload["context_signature_id"] = canonical_hash("semantic-context-signature", payload)
    return SemanticContextSignature.model_validate(payload)


def build_semantic_facet_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    draft: SemanticFacetSetDraft,
) -> SemanticFacetSet:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_map = MarkdownDocumentMap.model_validate(document_map.model_dump(mode="json"))
    trusted_atoms = SourceAtomSet.model_validate(atom_set.model_dump(mode="json"))
    trusted_fragments = SourceFragmentSet.model_validate(fragment_set.model_dump(mode="json"))
    trusted_draft = SemanticFacetSetDraft.model_validate(draft.model_dump(mode="json"))
    verify_markdown_document_map(trusted_document, trusted_map)
    verify_source_fragment_set(
        trusted_document,
        trusted_map,
        trusted_atoms,
        trusted_fragments,
    )
    if trusted_draft.document_id != trusted_document.document_id:
        raise ValueError("Semantic Facet draft document_id does not match the source document")

    order = _fragment_order(trusted_fragments)
    known_fragment_ids = set(order)
    classified_fragment_ids: set[str] = set()
    facets: list[SemanticFacet] = []
    for draft_facet in trusted_draft.facets:
        primary_ids = set(draft_facet.context.primary_fragment_ids)
        context_ids = set(draft_facet.context.required_context_fragment_ids)
        unknown = (primary_ids | context_ids) - known_fragment_ids
        if unknown:
            raise ValueError("Semantic Facet references an unknown Source Fragment")
        classified_fragment_ids.update(primary_ids)
        context = _build_context_signature(draft_facet.context, order)
        facet_payload = _SemanticFacetPayload(
            display_title=draft_facet.display_title,
            category_kinds=tuple(sorted(draft_facet.category_kinds)),
            retrieval_aliases=tuple(sorted(draft_facet.retrieval_aliases)),
            context=context,
        ).model_dump(mode="json")
        facet_payload["facet_id"] = canonical_hash("semantic-facet", facet_payload)
        facets.append(SemanticFacet.model_validate(facet_payload))

    facets.sort(key=lambda item: item.facet_id)
    facet_ids = tuple(facet.facet_id for facet in facets)
    if len(facet_ids) != len(set(facet_ids)):
        raise ValueError("Semantic Facet draft contains duplicate canonical Facets")

    unclassified_fragment_ids = set(trusted_draft.unclassified_fragment_ids)
    unknown_unclassified = unclassified_fragment_ids - known_fragment_ids
    if unknown_unclassified:
        raise ValueError("unclassified_fragment_ids contains an unknown Source Fragment")
    if classified_fragment_ids & unclassified_fragment_ids:
        raise ValueError("classified and unclassified Source Fragments must be disjoint")
    if classified_fragment_ids | unclassified_fragment_ids != known_fragment_ids:
        raise ValueError(
            "Semantic Facet draft must cover every Source Fragment with a Facet or "
            "unclassified fallback"
        )

    payload = _SemanticFacetSetPayload(
        document_id=trusted_document.document_id,
        source_ref=trusted_document.source_ref,
        normalized_body_hash=trusted_map.normalized_body_hash,
        document_map_id=trusted_map.map_id,
        atom_set_id=trusted_atoms.atom_set_id,
        fragment_set_id=trusted_fragments.fragment_set_id,
        facets=tuple(facets),
        unclassified_fragment_ids=tuple(
            sorted(unclassified_fragment_ids, key=order.__getitem__)
        ),
        use_scope=SEMANTIC_FACET_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
        qualification=SEMANTIC_FACET_QUALIFICATION,
    ).model_dump(mode="json")
    payload["facet_set_id"] = canonical_hash("semantic-facet-set", payload)
    return SemanticFacetSet.model_validate(payload)


def verify_semantic_facet_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    facet_set: SemanticFacetSet,
) -> None:
    validated = SemanticFacetSet.model_validate(facet_set.model_dump(mode="json"))
    draft = SemanticFacetSetDraft(
        document_id=validated.document_id,
        facets=tuple(
            SemanticFacetDraft(
                display_title=facet.display_title,
                category_kinds=facet.category_kinds,
                retrieval_aliases=facet.retrieval_aliases,
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=facet.context.primary_fragment_ids,
                    required_context_fragment_ids=facet.context.required_context_fragment_ids,
                    subject_terms=facet.context.subject_terms,
                    component_terms=facet.context.component_terms,
                    role_terms=facet.context.role_terms,
                    scenario_terms=facet.context.scenario_terms,
                    operation_terms=facet.context.operation_terms,
                    condition_terms=facet.context.condition_terms,
                    version_terms=facet.context.version_terms,
                ),
            )
            for facet in validated.facets
        ),
        unclassified_fragment_ids=validated.unclassified_fragment_ids,
    )
    rebuilt = build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        draft,
    )
    if rebuilt != validated:
        raise ValueError("Semantic Facet Set does not match the trusted source inputs")


def _fragments_by_atom(fragment_set: SourceFragmentSet) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for fragment in fragment_set.fragments:
        grouped.setdefault(fragment.atom_id, []).append(fragment.fragment_id)
    return {atom_id: tuple(fragment_ids) for atom_id, fragment_ids in grouped.items()}


def build_semantic_facet_set_from_projection_mapping(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    mapping: DocumentProjectionMapping,
) -> SemanticFacetSet:
    trusted_mapping = DocumentProjectionMapping.model_validate(mapping.model_dump(mode="json"))
    verify_document_projection_mapping(document, document_map, atom_set, trusted_mapping)
    verify_source_fragment_set(document, document_map, atom_set, fragment_set)
    grouped = _fragments_by_atom(fragment_set)
    atom_order = {atom.atom_id: atom.ordinal for atom in atom_set.atoms}

    facets: list[SemanticFacetDraft] = []
    for binding in trusted_mapping.bindings:
        if not binding.subject_terms:
            raise ValueError(
                "legacy Projection binding with empty subject_terms cannot be converted "
                "to Semantic Context without inventing source metadata"
            )
        primary_atom_ids = tuple(sorted(binding.atom_ids, key=atom_order.__getitem__))
        context_atom_ids = tuple(
            sorted(binding.required_context_atom_ids, key=atom_order.__getitem__)
        )
        primary_fragment_ids = tuple(
            fragment_id for atom_id in primary_atom_ids for fragment_id in grouped[atom_id]
        )
        required_context_fragment_ids = tuple(
            fragment_id for atom_id in context_atom_ids for fragment_id in grouped[atom_id]
        )
        facets.append(
            SemanticFacetDraft(
                display_title=binding.display_title,
                category_kinds=(binding.category_kind,),
                retrieval_aliases=binding.retrieval_aliases,
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=primary_fragment_ids,
                    required_context_fragment_ids=required_context_fragment_ids,
                    subject_terms=binding.subject_terms,
                ),
            )
        )

    unclassified_fragment_ids = tuple(
        fragment_id
        for atom_id in sorted(trusted_mapping.unclassified_atom_ids, key=atom_order.__getitem__)
        for fragment_id in grouped[atom_id]
    )
    return build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        SemanticFacetSetDraft(
            document_id=document.document_id,
            facets=tuple(facets),
            unclassified_fragment_ids=unclassified_fragment_ids,
        ),
    )


def build_projection_mapping_from_semantic_facet_set(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    facet_set: SemanticFacetSet,
) -> DocumentProjectionMapping:
    trusted_facets = SemanticFacetSet.model_validate(facet_set.model_dump(mode="json"))
    verify_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        trusted_facets,
    )
    fragments_by_id = {
        fragment.fragment_id: fragment for fragment in fragment_set.fragments
    }
    atom_order = {atom.atom_id: atom.ordinal for atom in atom_set.atoms}

    classified_fragment_ids = {
        fragment_id
        for facet in trusted_facets.facets
        for fragment_id in facet.context.primary_fragment_ids
    }
    unclassified_fragment_ids = set(trusted_facets.unclassified_fragment_ids)
    grouped = _fragments_by_atom(fragment_set)
    for atom_id, fragment_ids in grouped.items():
        fragment_id_set = set(fragment_ids)
        if fragment_id_set & classified_fragment_ids and fragment_id_set & (
            unclassified_fragment_ids
        ):
            raise ValueError(
                "Semantic Facet Set cannot be collapsed to Projection v1 because Atom "
                f"{atom_id} mixes classified and explicitly unclassified Fragments"
            )

    def ordered_parent_atom_ids(fragment_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {fragments_by_id[fragment_id].atom_id for fragment_id in fragment_ids},
                key=atom_order.__getitem__,
            )
        )

    bindings: list[ProjectionBindingDraft] = []
    for facet in trusted_facets.facets:
        primary_atom_ids = ordered_parent_atom_ids(facet.context.primary_fragment_ids)
        context_atom_ids = tuple(
            atom_id
            for atom_id in ordered_parent_atom_ids(
                facet.context.required_context_fragment_ids
            )
            if atom_id not in set(primary_atom_ids)
        )
        subject_terms = tuple(
            sorted(set(facet.context.subject_terms) | set(facet.context.component_terms))
        )
        for category_kind in facet.category_kinds:
            bindings.append(
                ProjectionBindingDraft(
                    category_kind=category_kind,
                    display_title=facet.display_title,
                    subject_terms=subject_terms,
                    retrieval_aliases=facet.retrieval_aliases,
                    atom_ids=primary_atom_ids,
                    required_context_atom_ids=context_atom_ids,
                )
            )

    collapsed_binding_keys = tuple(
        canonical_hash(
            "semantic-facet-projection-v1-collapse",
            binding.model_dump(mode="json"),
        )
        for binding in bindings
    )
    if len(collapsed_binding_keys) != len(set(collapsed_binding_keys)):
        raise ValueError(
            "Semantic Facet Set cannot be collapsed to Projection v1 because distinct "
            "Facets become the same legacy Binding"
        )

    unclassified_atom_ids = tuple(
        atom.atom_id
        for atom in atom_set.atoms
        if set(grouped[atom.atom_id]) <= unclassified_fragment_ids
    )
    return build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        DocumentProjectionMappingDraft(
            document_id=document.document_id,
            bindings=tuple(bindings),
            unclassified_atom_ids=unclassified_atom_ids,
        ),
    )


class SemanticFacetRelationDraft(FrozenModel):
    relation_kind: SemanticRelationKind
    source_facet_id: Annotated[str, Field(pattern=_FACET_ID_RE.pattern)]
    target_facet_id: Annotated[str, Field(pattern=_FACET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        if self.source_facet_id == self.target_facet_id:
            raise ValueError("Semantic Facet relation cannot reference the same Facet twice")
        return self


class SemanticRelationGraphDraft(FrozenModel):
    schema_version: Literal["semantic-relation-graph-draft-v1"] = (
        SEMANTIC_RELATION_GRAPH_DRAFT_SCHEMA_VERSION
    )
    document_id: Annotated[str, Field(min_length=1)]
    relations: tuple[SemanticFacetRelationDraft, ...] = ()

    @field_validator("relations", mode="before")
    @classmethod
    def parse_relations(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "SemanticRelationGraphDraft.relations")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticRelationGraphDraft.document_id")


class _SemanticFacetRelationFields(FrozenModel):
    relation_kind: SemanticRelationKind
    source_facet_id: Annotated[str, Field(pattern=_FACET_ID_RE.pattern)]
    target_facet_id: Annotated[str, Field(pattern=_FACET_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        if self.source_facet_id == self.target_facet_id:
            raise ValueError("Semantic Facet relation cannot reference the same Facet twice")
        if (
            self.relation_kind in _SYMMETRIC_RELATION_KINDS
            and self.source_facet_id > self.target_facet_id
        ):
            raise ValueError("symmetric Semantic Facet relation endpoints must be sorted")
        return self


class _SemanticFacetRelationPayload(_SemanticFacetRelationFields):
    pass


class SemanticFacetRelation(_SemanticFacetRelationFields):
    relation_id: Annotated[str, Field(pattern=_RELATION_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_relation_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"relation_id"})
        if self.relation_id != canonical_hash("semantic-facet-relation", payload):
            raise ValueError(
                "SemanticFacetRelation.relation_id does not match its complete contents"
            )
        return self


class _SemanticRelationGraphFields(FrozenModel):
    schema_version: Literal["semantic-relation-graph-v1"] = (
        SEMANTIC_RELATION_GRAPH_SCHEMA_VERSION
    )
    document_id: Annotated[str, Field(min_length=1)]
    source_ref: SourceRef
    normalized_body_hash: Annotated[str, Field(pattern=_SHA256_RE.pattern)]
    facet_set_id: Annotated[str, Field(pattern=_FACET_SET_ID_RE.pattern)]
    relations: tuple[SemanticFacetRelation, ...] = ()
    use_scope: Literal["retrieval_navigation_only_not_evidence"] = SEMANTIC_FACET_USE_SCOPE
    evidence_eligible: Literal[False] = False
    production_qualified: Literal[False] = False
    qualification: Literal[
        "mechanically_verified_relations_not_semantically_reviewed"
    ] = SEMANTIC_RELATION_QUALIFICATION

    @field_validator("source_ref", mode="before")
    @classmethod
    def parse_source_ref(cls, value: object) -> object:
        return _strict_source_ref(value, "SemanticRelationGraph.source_ref")

    @field_validator("relations", mode="before")
    @classmethod
    def parse_relations(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "SemanticRelationGraph.relations")

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _trimmed_single_line(value, "SemanticRelationGraph.document_id")

    @model_validator(mode="after")
    def validate_relations(self) -> Self:
        relation_ids = tuple(relation.relation_id for relation in self.relations)
        if relation_ids != tuple(sorted(relation_ids)):
            raise ValueError("SemanticRelationGraph.relations must be sorted by relation_id")
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("SemanticRelationGraph relation IDs must be unique")
        return self


class _SemanticRelationGraphPayload(_SemanticRelationGraphFields):
    pass


class SemanticRelationGraph(_SemanticRelationGraphFields):
    relation_graph_id: Annotated[str, Field(pattern=_RELATION_GRAPH_ID_RE.pattern)]

    @model_validator(mode="after")
    def validate_relation_graph_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"relation_graph_id"})
        if self.relation_graph_id != canonical_hash("semantic-relation-graph", payload):
            raise ValueError(
                "SemanticRelationGraph.relation_graph_id does not match its complete contents"
            )
        return self


def build_semantic_relation_graph(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    facet_set: SemanticFacetSet,
    draft: SemanticRelationGraphDraft,
) -> SemanticRelationGraph:
    trusted_document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    trusted_draft = SemanticRelationGraphDraft.model_validate(draft.model_dump(mode="json"))
    trusted_facets = SemanticFacetSet.model_validate(facet_set.model_dump(mode="json"))
    verify_semantic_facet_set(
        trusted_document,
        document_map,
        atom_set,
        fragment_set,
        trusted_facets,
    )
    if trusted_draft.document_id != trusted_document.document_id:
        raise ValueError("Semantic Relation draft document_id does not match source document")

    known_facet_ids = {facet.facet_id for facet in trusted_facets.facets}
    relations: list[SemanticFacetRelation] = []
    for draft_relation in trusted_draft.relations:
        if {
            draft_relation.source_facet_id,
            draft_relation.target_facet_id,
        } - known_facet_ids:
            raise ValueError("Semantic Facet relation references an unknown Facet")
        source_id = draft_relation.source_facet_id
        target_id = draft_relation.target_facet_id
        if draft_relation.relation_kind in _SYMMETRIC_RELATION_KINDS:
            source_id, target_id = sorted((source_id, target_id))
        relation_payload = _SemanticFacetRelationPayload(
            relation_kind=draft_relation.relation_kind,
            source_facet_id=source_id,
            target_facet_id=target_id,
        ).model_dump(mode="json")
        relation_payload["relation_id"] = canonical_hash(
            "semantic-facet-relation",
            relation_payload,
        )
        relations.append(SemanticFacetRelation.model_validate(relation_payload))

    relations.sort(key=lambda item: item.relation_id)
    relation_ids = tuple(relation.relation_id for relation in relations)
    if len(relation_ids) != len(set(relation_ids)):
        raise ValueError("Semantic Relation draft contains duplicate canonical relations")

    payload = _SemanticRelationGraphPayload(
        document_id=trusted_document.document_id,
        source_ref=trusted_document.source_ref,
        normalized_body_hash=trusted_facets.normalized_body_hash,
        facet_set_id=trusted_facets.facet_set_id,
        relations=tuple(relations),
        use_scope=SEMANTIC_FACET_USE_SCOPE,
        evidence_eligible=False,
        production_qualified=False,
        qualification=SEMANTIC_RELATION_QUALIFICATION,
    ).model_dump(mode="json")
    payload["relation_graph_id"] = canonical_hash("semantic-relation-graph", payload)
    return SemanticRelationGraph.model_validate(payload)


def verify_semantic_relation_graph(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    facet_set: SemanticFacetSet,
    relation_graph: SemanticRelationGraph,
) -> None:
    validated = SemanticRelationGraph.model_validate(relation_graph.model_dump(mode="json"))
    draft = SemanticRelationGraphDraft(
        document_id=validated.document_id,
        relations=tuple(
            SemanticFacetRelationDraft(
                relation_kind=relation.relation_kind,
                source_facet_id=relation.source_facet_id,
                target_facet_id=relation.target_facet_id,
            )
            for relation in validated.relations
        ),
    )
    rebuilt = build_semantic_relation_graph(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
        draft,
    )
    if rebuilt != validated:
        raise ValueError("Semantic Relation Graph does not match the trusted Facet Set")


def load_semantic_facet_set(
    raw: str | bytes,
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
) -> SemanticFacetSet:
    facet_set = load_json_model(raw, SemanticFacetSet, "Semantic Facet Set")
    verify_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
    )
    return facet_set


def load_semantic_facet_set_draft(raw: str | bytes) -> SemanticFacetSetDraft:
    draft = load_json_model(raw, SemanticFacetSetDraft, "Semantic Facet Set Draft")
    _require_explicit_fields(
        draft,
        frozenset({"schema_version", "document_id", "facets", "unclassified_fragment_ids"}),
        "Semantic Facet Set Draft",
    )
    for index, facet in enumerate(draft.facets):
        _require_explicit_fields(
            facet,
            frozenset({"display_title", "category_kinds", "retrieval_aliases", "context"}),
            f"Semantic Facet Set Draft facet {index}",
        )
        _require_explicit_fields(
            facet.context,
            frozenset(
                {
                    "primary_fragment_ids",
                    "required_context_fragment_ids",
                    "subject_terms",
                    "component_terms",
                    "role_terms",
                    "scenario_terms",
                    "operation_terms",
                    "condition_terms",
                    "version_terms",
                }
            ),
            f"Semantic Facet Set Draft facet {index} context",
        )
    return draft


def load_semantic_relation_graph(
    raw: str | bytes,
    *,
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    atom_set: SourceAtomSet,
    fragment_set: SourceFragmentSet,
    facet_set: SemanticFacetSet,
) -> SemanticRelationGraph:
    relation_graph = load_json_model(raw, SemanticRelationGraph, "Semantic Relation Graph")
    verify_semantic_relation_graph(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
        relation_graph,
    )
    return relation_graph


def load_semantic_relation_graph_draft(raw: str | bytes) -> SemanticRelationGraphDraft:
    draft = load_json_model(raw, SemanticRelationGraphDraft, "Semantic Relation Graph Draft")
    _require_explicit_fields(
        draft,
        frozenset({"schema_version", "document_id", "relations"}),
        "Semantic Relation Graph Draft",
    )
    for index, relation in enumerate(draft.relations):
        _require_explicit_fields(
            relation,
            frozenset({"relation_kind", "source_facet_id", "target_facet_id"}),
            f"Semantic Relation Graph Draft relation {index}",
        )
    return draft


__all__ = [
    "SEMANTIC_CONTEXT_POLICY_VERSION",
    "SEMANTIC_FACET_QUALIFICATION",
    "SEMANTIC_FACET_SET_DRAFT_SCHEMA_VERSION",
    "SEMANTIC_FACET_SET_SCHEMA_VERSION",
    "SEMANTIC_FACET_USE_SCOPE",
    "SEMANTIC_RELATION_GRAPH_DRAFT_SCHEMA_VERSION",
    "SEMANTIC_RELATION_GRAPH_SCHEMA_VERSION",
    "SEMANTIC_RELATION_KINDS",
    "SEMANTIC_RELATION_QUALIFICATION",
    "SemanticContextSignature",
    "SemanticContextSignatureDraft",
    "SemanticFacet",
    "SemanticFacetDraft",
    "SemanticFacetRelation",
    "SemanticFacetRelationDraft",
    "SemanticFacetSet",
    "SemanticFacetSetDraft",
    "SemanticRelationGraph",
    "SemanticRelationGraphDraft",
    "SemanticRelationKind",
    "build_projection_mapping_from_semantic_facet_set",
    "build_semantic_facet_set",
    "build_semantic_facet_set_from_projection_mapping",
    "build_semantic_relation_graph",
    "load_semantic_facet_set",
    "load_semantic_facet_set_draft",
    "load_semantic_relation_graph",
    "load_semantic_relation_graph_draft",
    "verify_semantic_facet_set",
    "verify_semantic_relation_graph",
]
