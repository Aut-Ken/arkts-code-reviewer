from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.knowledge.build import NormalizedKnowledgeBuild
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    ClauseCandidate,
    NormalizedDocument,
)
from arkts_code_reviewer.knowledge.parsing import (
    ExtractedClause,
    parse_api_symbols,
    parse_markdown_clauses,
)

EXTRACTION_BUILD_SCHEMA_VERSION = "knowledge-extraction-build-v1"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"knowledge-extraction:sha256:{hashlib.sha256(raw).hexdigest()}"


class ExtractedKnowledgeDocument(_FrozenModel):
    document_id: Annotated[str, Field(min_length=1)]
    domains: tuple[str, ...]
    clauses: tuple[ExtractedClause, ...]
    api_symbols: tuple[ApiSymbol, ...]
    diagnostics: tuple[str, ...]

    @field_validator("domains", "diagnostics")
    @classmethod
    def validate_sorted_strings(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        if list(value) != sorted(set(value)):
            raise ValueError(
                f"ExtractedKnowledgeDocument.{info.field_name} must be sorted and unique"
            )
        return value

    @model_validator(mode="after")
    def validate_output_order(self) -> ExtractedKnowledgeDocument:
        clause_ids = [item.rule_id for item in self.clauses]
        if clause_ids != sorted(set(clause_ids)):
            raise ValueError("Extracted Knowledge clauses must be sorted and unique")
        symbol_keys = [
            (
                item.canonical_name,
                item.signature,
                item.source_span.start_line,
                item.source_span.end_line,
            )
            for item in self.api_symbols
        ]
        if symbol_keys != sorted(set(symbol_keys)):
            raise ValueError("Extracted API declarations must be sorted and unique")
        return self


class KnowledgeExtractionBuild(_FrozenModel):
    schema_version: Literal["knowledge-extraction-build-v1"] = (
        "knowledge-extraction-build-v1"
    )
    build_id: Annotated[str, Field(pattern=r"^knowledge-extraction:sha256:[0-9a-f]{64}$")]
    normalized_build_id: Annotated[
        str,
        Field(pattern=r"^knowledge-build:sha256:[0-9a-f]{64}$"),
    ]
    source_bundle_id: Annotated[str, Field(pattern=r"^source-bundle:sha256:[0-9a-f]{64}$")]
    seed_fingerprint: Annotated[str, Field(pattern=r"^knowledge-seed:sha256:[0-9a-f]{64}$")]
    parser_versions: tuple[str, ...]
    documents: tuple[ExtractedKnowledgeDocument, ...]

    @field_validator("parser_versions")
    @classmethod
    def validate_parser_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or list(value) != sorted(set(value)):
            raise ValueError("Knowledge parser_versions must be non-empty, sorted, and unique")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> KnowledgeExtractionBuild:
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != 24 or document_ids != sorted(set(document_ids)):
            raise ValueError("Knowledge extraction must contain 24 sorted documents")
        rule_ids = [clause.rule_id for item in self.documents for clause in item.clauses]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Knowledge extraction contains cross-document duplicate rule IDs")
        declaration_ids = [
            symbol.declaration_id for item in self.documents for symbol in item.api_symbols
        ]
        if len(declaration_ids) != len(set(declaration_ids)):
            raise ValueError("Knowledge extraction contains duplicate API declarations")
        if self.build_id != _canonical_hash(self.identity_payload()):
            raise ValueError("KnowledgeExtractionBuild.build_id does not match content")
        return self

    def identity_payload(self) -> dict[str, object]:
        return {
            "normalized_build_id": self.normalized_build_id,
            "source_bundle_id": self.source_bundle_id,
            "seed_fingerprint": self.seed_fingerprint,
            "parser_versions": self.parser_versions,
            "documents": [item.model_dump(mode="json") for item in self.documents],
        }


def _check_spans(
    extracted: ExtractedKnowledgeDocument,
    source_document: NormalizedDocument,
) -> None:
    items: list[ClauseCandidate | ApiSymbol] = [
        *(item.candidate for item in extracted.clauses),
        *extracted.api_symbols,
    ]
    line_count = len(source_document.body.splitlines())
    expected_source = source_document.source_ref
    for item in items:
        if item.source_span.end_line > line_count:
            raise ValueError("Knowledge extraction emitted an out-of-range source span")
        actual_source = item.source_ref
        if (
            actual_source.source_id != expected_source.source_id
            or actual_source.revision != expected_source.revision
            or actual_source.relative_path != expected_source.relative_path
            or actual_source.authority != expected_source.authority
            or actual_source.content_hash != expected_source.content_hash
        ):
            raise ValueError("Knowledge extraction source provenance drift")
        expected_anchor = f"L{item.source_span.start_line}-L{item.source_span.end_line}"
        if actual_source.anchor != expected_anchor:
            raise ValueError("Knowledge extraction source anchor does not match span")
    for clause in extracted.clauses:
        if any(example.source_span.end_line > line_count for example in clause.candidate.examples):
            raise ValueError("Knowledge extraction emitted an out-of-range example span")


def build_knowledge_extraction(
    normalized: NormalizedKnowledgeBuild,
) -> KnowledgeExtractionBuild:
    documents: list[ExtractedKnowledgeDocument] = []
    parser_versions: set[str] = set()
    for normalized_item in normalized.documents:
        document = normalized_item.document
        if document.media_type == "text/markdown":
            clause_result = parse_markdown_clauses(document)
            clauses = clause_result.clauses
            api_symbols: tuple[ApiSymbol, ...] = ()
            diagnostics = clause_result.diagnostics
            parser_versions.add(clause_result.parser_version)
        elif document.media_type == "text/typescript-declaration":
            api_result = parse_api_symbols(document)
            clauses = ()
            api_symbols = api_result.symbols
            diagnostics = api_result.diagnostics
            parser_versions.add(api_result.parser_version)
        else:
            raise ValueError(f"unsupported normalized media type: {document.media_type}")
        extracted = ExtractedKnowledgeDocument(
            document_id=document.document_id,
            domains=normalized_item.domains,
            clauses=clauses,
            api_symbols=api_symbols,
            diagnostics=tuple(sorted(set((*document.diagnostics, *diagnostics)))),
        )
        _check_spans(extracted, document)
        documents.append(extracted)
    ordered = tuple(sorted(documents, key=lambda item: item.document_id))
    payload = {
        "normalized_build_id": normalized.build_id,
        "source_bundle_id": normalized.source_bundle_id,
        "seed_fingerprint": normalized.seed_fingerprint,
        "parser_versions": tuple(sorted(parser_versions)),
        "documents": [item.model_dump(mode="json") for item in ordered],
    }
    return KnowledgeExtractionBuild(
        build_id=_canonical_hash(payload),
        normalized_build_id=normalized.build_id,
        source_bundle_id=normalized.source_bundle_id,
        seed_fingerprint=normalized.seed_fingerprint,
        parser_versions=tuple(sorted(parser_versions)),
        documents=ordered,
    )


__all__ = [
    "EXTRACTION_BUILD_SCHEMA_VERSION",
    "ExtractedKnowledgeDocument",
    "KnowledgeExtractionBuild",
    "build_knowledge_extraction",
]
