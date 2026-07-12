from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    ParserLayer,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    normalize_review_path,
)

FILE_ANALYSIS_SCHEMA_VERSION = "file-analysis-v1"

OwnerKind = Literal["declaration", "region"]
AnalysisQuality = Literal["exact", "recovered", "degraded", "unresolved"]
StructuralQuality = Literal["exact", "recovered"]
FactProvenance = Literal["L0", "L1", "recovered"]
RegionKind = Literal["field_region", "import_region"]
FileAnalysisDiagnostic = Literal[
    "file_analysis_sidecar_unavailable",
    "file_analysis_sidecar_failed",
    "file_analysis_snapshot_invalid",
    "occurrence_extraction_unavailable",
    "unresolved_fact_owner",
    "parser_error_nodes",
    "parser_missing_nodes",
    "ambiguous_binding_scope",
]
UnitFactDiagnostic = Literal["unit_owner_unresolved"]
FactKind = Literal[
    "component",
    "api",
    "decorator",
    "attribute",
    "symbol",
    "syntax",
    "import_binding",
    "import_use",
    "field_read",
    "field_write",
    "call",
    "string_literal",
    "resource_reference",
]

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DECLARATION_KINDS = {
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
}
_FILE_ANALYSIS_DIAGNOSTICS = {
    "file_analysis_sidecar_unavailable",
    "file_analysis_sidecar_failed",
    "file_analysis_snapshot_invalid",
    "occurrence_extraction_unavailable",
    "unresolved_fact_owner",
    "parser_error_nodes",
    "parser_missing_nodes",
    "ambiguous_binding_scope",
}
_UNIT_FACT_DIAGNOSTICS = {"unit_owner_unresolved"}


def _canonical_id(prefix: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _content_hash(content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("source content must be a string")
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _non_empty(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must not contain control characters")
    return value


def _sorted_unique_strings(values: tuple[str, ...], context: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{context} must contain non-empty strings")
    if list(values) != sorted(set(values)):
        raise ValueError(f"{context} must be sorted and unique")


def _span_key(span: SourceSpan, exact_range: ExactRange) -> tuple[object, ...]:
    return (
        span.start_line,
        exact_range.start_offset_utf16,
        span.end_line,
        exact_range.end_offset_utf16,
    )


@dataclass(frozen=True)
class CodeSourceRef:
    source_ref_id: str
    repository: str
    revision: str
    path: str
    content_hash: str

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def create(
        cls,
        *,
        repository: str,
        revision: str,
        path: str,
        content_hash: str,
    ) -> CodeSourceRef:
        normalized_path = normalize_review_path(path)
        source_ref_id = cls.expected_id(
            repository=repository,
            revision=revision,
            path=normalized_path,
            content_hash=content_hash,
        )
        return cls(
            source_ref_id=source_ref_id,
            repository=repository,
            revision=revision,
            path=normalized_path,
            content_hash=content_hash,
        )

    @classmethod
    def inline(
        cls,
        path: str,
        content: str,
        *,
        repository: str = "inline",
    ) -> CodeSourceRef:
        content_hash = _content_hash(content)
        return cls.create(
            repository=repository,
            revision=f"snapshot:{content_hash}",
            path=path,
            content_hash=content_hash,
        )

    @staticmethod
    def expected_id(
        *,
        repository: str,
        revision: str,
        path: str,
        content_hash: str,
    ) -> str:
        return _canonical_id(
            "code-source",
            {
                "repository": _non_empty(repository, "CodeSourceRef.repository"),
                "revision": _non_empty(revision, "CodeSourceRef.revision"),
                "path": normalize_review_path(path),
                "content_hash": content_hash,
            },
        )

    def validate(self) -> None:
        _non_empty(self.repository, "CodeSourceRef.repository")
        _non_empty(self.revision, "CodeSourceRef.revision")
        if self.path != normalize_review_path(self.path):
            raise ValueError("CodeSourceRef.path must be normalized")
        if not isinstance(self.content_hash, str) or not _SHA256_RE.fullmatch(
            self.content_hash
        ):
            raise ValueError("CodeSourceRef.content_hash must use sha256:<64 lowercase hex>")
        expected = self.expected_id(
            repository=self.repository,
            revision=self.revision,
            path=self.path,
            content_hash=self.content_hash,
        )
        if self.source_ref_id != expected:
            raise ValueError("CodeSourceRef.source_ref_id does not match its fields")

    def verify_content(self, content: str) -> None:
        if _content_hash(content) != self.content_hash:
            raise ValueError("CodeSourceRef content hash mismatch")


@dataclass(frozen=True)
class ExactRange:
    start_line: int
    end_line: int
    start_offset_utf16: int
    end_offset_utf16: int

    def __post_init__(self) -> None:
        for value, context, minimum in (
            (self.start_line, "ExactRange.start_line", 1),
            (self.end_line, "ExactRange.end_line", 1),
            (self.start_offset_utf16, "ExactRange.start_offset_utf16", 0),
            (self.end_offset_utf16, "ExactRange.end_offset_utf16", 0),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{context} must be an integer >= {minimum}")
        if self.end_line < self.start_line:
            raise ValueError("ExactRange.end_line must be >= start_line")
        if self.end_offset_utf16 < self.start_offset_utf16:
            raise ValueError(
                "ExactRange.end_offset_utf16 must be >= start_offset_utf16"
            )

    def contains(self, other: ExactRange) -> bool:
        return (
            self.start_offset_utf16 <= other.start_offset_utf16
            and other.end_offset_utf16 <= self.end_offset_utf16
        )


@dataclass(frozen=True)
class OwnerRef:
    kind: OwnerKind
    ref_id: str

    def __post_init__(self) -> None:
        if self.kind not in {"declaration", "region"}:
            raise ValueError(f"unsupported OwnerRef kind: {self.kind}")
        _non_empty(self.ref_id, "OwnerRef.ref_id")
        prefix = "declaration:" if self.kind == "declaration" else "region:"
        if not self.ref_id.startswith(prefix):
            raise ValueError("OwnerRef.ref_id prefix must match OwnerRef.kind")


@dataclass(frozen=True)
class DeclarationOccurrence:
    declaration_id: str
    kind: str
    name: str
    qualified_name: str
    span: SourceSpan
    exact_range: ExactRange
    parent_id: str | None = None
    quality: StructuralQuality = "exact"

    @classmethod
    def create(
        cls,
        *,
        source_ref_id: str,
        kind: str,
        name: str,
        qualified_name: str,
        span: SourceSpan,
        exact_range: ExactRange,
        parent_id: str | None = None,
        quality: StructuralQuality = "exact",
    ) -> DeclarationOccurrence:
        return cls(
            declaration_id=cls.expected_id(
                source_ref_id, kind, qualified_name, span, exact_range
            ),
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            span=span,
            exact_range=exact_range,
            parent_id=parent_id,
            quality=quality,
        )

    @staticmethod
    def expected_id(
        source_ref_id: str,
        kind: str,
        qualified_name: str,
        span: SourceSpan,
        exact_range: ExactRange,
    ) -> str:
        return _canonical_id(
            "declaration",
            {
                "source_ref_id": source_ref_id,
                "kind": kind,
                "qualified_name": qualified_name,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "start_offset_utf16": exact_range.start_offset_utf16,
                "end_offset_utf16": exact_range.end_offset_utf16,
            },
        )

    def __post_init__(self) -> None:
        if self.kind not in _DECLARATION_KINDS:
            raise ValueError(f"unsupported declaration occurrence kind: {self.kind}")
        _non_empty(self.name, "DeclarationOccurrence.name")
        _non_empty(self.qualified_name, "DeclarationOccurrence.qualified_name")
        _validate_span_pair(self.span, self.exact_range, "DeclarationOccurrence")
        _validate_structural_quality(self.quality)
        if self.parent_id is not None:
            _non_empty(self.parent_id, "DeclarationOccurrence.parent_id")


@dataclass(frozen=True)
class ReviewRegion:
    region_id: str
    kind: RegionKind
    symbol: str
    span: SourceSpan
    exact_range: ExactRange
    owner_declaration_id: str | None = None
    quality: StructuralQuality = "exact"
    provenance: FactProvenance = "L1"

    @classmethod
    def create(
        cls,
        *,
        source_ref_id: str,
        kind: RegionKind,
        symbol: str,
        span: SourceSpan,
        exact_range: ExactRange,
        owner_declaration_id: str | None = None,
        quality: StructuralQuality = "exact",
        provenance: FactProvenance = "L1",
    ) -> ReviewRegion:
        return cls(
            region_id=cls.expected_id(
                source_ref_id,
                kind,
                symbol,
                span,
                exact_range,
                owner_declaration_id,
            ),
            kind=kind,
            symbol=symbol,
            span=span,
            exact_range=exact_range,
            owner_declaration_id=owner_declaration_id,
            quality=quality,
            provenance=provenance,
        )

    @staticmethod
    def expected_id(
        source_ref_id: str,
        kind: RegionKind,
        symbol: str,
        span: SourceSpan,
        exact_range: ExactRange,
        owner_declaration_id: str | None,
    ) -> str:
        return _canonical_id(
            "region",
            {
                "source_ref_id": source_ref_id,
                "kind": kind,
                "symbol": symbol,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "start_offset_utf16": exact_range.start_offset_utf16,
                "end_offset_utf16": exact_range.end_offset_utf16,
                "owner_declaration_id": owner_declaration_id,
            },
        )

    def __post_init__(self) -> None:
        if self.kind not in {"field_region", "import_region"}:
            raise ValueError(f"unsupported ReviewRegion kind: {self.kind}")
        _non_empty(self.symbol, "ReviewRegion.symbol")
        _validate_span_pair(self.span, self.exact_range, "ReviewRegion")
        _validate_structural_quality(self.quality)
        _validate_provenance(self.provenance)
        if self.quality == "exact" and self.provenance != "L1":
            raise ValueError(
                "exact ReviewRegion requires L1 provenance"
            )
        if self.quality == "recovered" and self.provenance != "recovered":
            raise ValueError(
                "recovered ReviewRegion requires recovered provenance"
            )
        if self.kind == "field_region" and self.owner_declaration_id is None:
            raise ValueError("field_region requires owner_declaration_id")
        if self.kind == "import_region" and self.owner_declaration_id is not None:
            raise ValueError("import_region must not have owner_declaration_id")


@dataclass(frozen=True)
class FactOccurrence:
    occurrence_id: str
    kind: FactKind
    name: str
    canonical_name: str | None
    span: SourceSpan
    exact_range: ExactRange
    owner_ref: OwnerRef | None
    quality: AnalysisQuality = "exact"
    provenance: FactProvenance = "L1"

    @classmethod
    def create(
        cls,
        *,
        source_ref_id: str,
        kind: FactKind,
        name: str,
        canonical_name: str | None,
        span: SourceSpan,
        exact_range: ExactRange,
        owner_ref: OwnerRef | None,
        quality: AnalysisQuality = "exact",
        provenance: FactProvenance = "L1",
    ) -> FactOccurrence:
        return cls(
            occurrence_id=cls.expected_id(
                source_ref_id,
                kind,
                name,
                canonical_name,
                exact_range,
                owner_ref,
            ),
            kind=kind,
            name=name,
            canonical_name=canonical_name,
            span=span,
            exact_range=exact_range,
            owner_ref=owner_ref,
            quality=quality,
            provenance=provenance,
        )

    @staticmethod
    def expected_id(
        source_ref_id: str,
        kind: FactKind,
        name: str,
        canonical_name: str | None,
        exact_range: ExactRange,
        owner_ref: OwnerRef | None,
    ) -> str:
        return _canonical_id(
            "occurrence",
            {
                "source_ref_id": source_ref_id,
                "kind": kind,
                "name": name,
                "canonical_name": canonical_name,
                "start_offset_utf16": exact_range.start_offset_utf16,
                "end_offset_utf16": exact_range.end_offset_utf16,
                "owner": None if owner_ref is None else asdict(owner_ref),
            },
        )

    def __post_init__(self) -> None:
        if self.kind not in {
            "component",
            "api",
            "decorator",
            "attribute",
            "symbol",
            "syntax",
            "import_binding",
            "import_use",
            "field_read",
            "field_write",
            "call",
            "string_literal",
            "resource_reference",
        }:
            raise ValueError(f"unsupported FactOccurrence kind: {self.kind}")
        _non_empty(self.name, "FactOccurrence.name")
        if self.canonical_name is not None:
            _non_empty(self.canonical_name, "FactOccurrence.canonical_name")
        _validate_span_pair(self.span, self.exact_range, "FactOccurrence")
        _validate_quality(self.quality)
        _validate_provenance(self.provenance)
        if self.quality in {"exact", "recovered"} and self.owner_ref is None:
            raise ValueError("exact/recovered FactOccurrence requires owner_ref")
        if self.quality == "unresolved" and self.owner_ref is not None:
            raise ValueError("unresolved FactOccurrence must not have owner_ref")
        if self.quality == "exact" and self.provenance != "L1":
            raise ValueError("exact FactOccurrence requires L1 provenance")
        if self.quality == "recovered" and self.provenance != "recovered":
            raise ValueError("recovered FactOccurrence requires recovered provenance")
        if self.quality == "degraded" and self.provenance != "L0":
            raise ValueError("degraded FactOccurrence requires L0 provenance")


@dataclass(frozen=True)
class FileParserQuality:
    layer: ParserLayer
    error_nodes: int | None = None
    missing_nodes: int | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.layer not in {"L0", "L1", "parse_degraded"}:
            raise ValueError(f"unsupported parser layer: {self.layer}")
        for value, context in (
            (self.error_nodes, "FileParserQuality.error_nodes"),
            (self.missing_nodes, "FileParserQuality.missing_nodes"),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{context} must be null or a non-negative integer")
        if (self.error_nodes is None) != (self.missing_nodes is None):
            raise ValueError(
                "FileParserQuality error/missing node counts must be provided together"
            )
        if self.layer != "L1" and self.error_nodes is not None:
            raise ValueError(
                "FileParserQuality non-L1 layers require null node counts"
            )
        _sorted_unique_strings(self.warnings, "FileParserQuality.warnings")


@dataclass(frozen=True)
class ScopedFacts:
    components: tuple[str, ...] = ()
    apis: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    syntax: tuple[str, ...] = ()
    import_bindings: tuple[str, ...] = ()
    import_uses: tuple[str, ...] = ()
    field_reads: tuple[str, ...] = ()
    field_writes: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    string_literals: tuple[str, ...] = ()
    resource_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _sorted_unique_strings(getattr(self, name), f"ScopedFacts.{name}")

    @classmethod
    def from_code_facts(cls, facts: CodeFacts) -> ScopedFacts:
        bindings = {
            binding
            for item in facts.imports
            for binding in (
                item.default_name,
                item.namespace_name,
                *item.named.keys(),
            )
            if binding
        }
        return cls(
            components=tuple(sorted(facts.components)),
            apis=tuple(sorted(facts.apis)),
            decorators=tuple(sorted(facts.decorators)),
            attributes=tuple(sorted(facts.attributes)),
            symbols=tuple(sorted(facts.symbols)),
            syntax=tuple(sorted(facts.syntax)),
            import_bindings=tuple(sorted(bindings)),
        )

    def to_code_facts(
        self,
        path: str,
        *,
        parser_layer: ParserLayer = "L1",
    ) -> CodeFacts:
        return CodeFacts(
            path=path,
            components=set(self.components),
            apis=set(self.apis),
            decorators=set(self.decorators),
            attributes=set(self.attributes),
            symbols=set(self.symbols),
            syntax=set(self.syntax),
            parser_layer=parser_layer,
        )


@dataclass(frozen=True)
class UnitFactScope:
    unit_id: str
    source_ref_id: str
    unit_exact: ScopedFacts
    file_hints: ScopedFacts
    exact_occurrence_ids: tuple[str, ...] = ()
    diagnostics: tuple[UnitFactDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.unit_id, "UnitFactScope.unit_id")
        _non_empty(self.source_ref_id, "UnitFactScope.source_ref_id")
        if not isinstance(self.unit_exact, ScopedFacts):
            raise ValueError("UnitFactScope.unit_exact must use ScopedFacts")
        if not isinstance(self.file_hints, ScopedFacts):
            raise ValueError("UnitFactScope.file_hints must use ScopedFacts")
        _sorted_unique_strings(
            self.exact_occurrence_ids,
            "UnitFactScope.exact_occurrence_ids",
        )
        _sorted_unique_strings(self.diagnostics, "UnitFactScope.diagnostics")
        unsupported_diagnostics = set(self.diagnostics) - _UNIT_FACT_DIAGNOSTICS
        if unsupported_diagnostics:
            raise ValueError(
                "UnitFactScope.diagnostics contains unsupported codes: "
                f"{sorted(unsupported_diagnostics)!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FileAnalysis:
    schema_version: str
    analysis_id: str
    source_ref: CodeSourceRef
    parser_version: str
    parser_quality: FileParserQuality
    file_hints: ScopedFacts
    declarations: tuple[DeclarationOccurrence, ...] = ()
    review_regions: tuple[ReviewRegion, ...] = ()
    fact_occurrences: tuple[FactOccurrence, ...] = ()
    diagnostics: tuple[FileAnalysisDiagnostic, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        source_ref: CodeSourceRef,
        parser_version: str,
        parser_quality: FileParserQuality,
        file_hints: ScopedFacts,
        declarations: tuple[DeclarationOccurrence, ...] = (),
        review_regions: tuple[ReviewRegion, ...] = (),
        fact_occurrences: tuple[FactOccurrence, ...] = (),
        diagnostics: tuple[FileAnalysisDiagnostic, ...] = (),
    ) -> FileAnalysis:
        return cls(
            schema_version=FILE_ANALYSIS_SCHEMA_VERSION,
            analysis_id=cls.expected_id(source_ref.source_ref_id, parser_version),
            source_ref=source_ref,
            parser_version=parser_version,
            parser_quality=parser_quality,
            file_hints=file_hints,
            declarations=declarations,
            review_regions=review_regions,
            fact_occurrences=fact_occurrences,
            diagnostics=diagnostics,
        )

    @staticmethod
    def expected_id(source_ref_id: str, parser_version: str) -> str:
        return _canonical_id(
            "file-analysis",
            {
                "schema_version": FILE_ANALYSIS_SCHEMA_VERSION,
                "source_ref_id": source_ref_id,
                "parser_version": parser_version,
            },
        )

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.source_ref, CodeSourceRef):
            raise ValueError("FileAnalysis.source_ref must use CodeSourceRef")
        if not isinstance(self.parser_quality, FileParserQuality):
            raise ValueError("FileAnalysis.parser_quality must use FileParserQuality")
        if not isinstance(self.file_hints, ScopedFacts):
            raise ValueError("FileAnalysis.file_hints must use ScopedFacts")
        if not isinstance(self.declarations, tuple) or any(
            not isinstance(item, DeclarationOccurrence) for item in self.declarations
        ):
            raise ValueError(
                "FileAnalysis.declarations must contain DeclarationOccurrence values"
            )
        if not isinstance(self.review_regions, tuple) or any(
            not isinstance(item, ReviewRegion) for item in self.review_regions
        ):
            raise ValueError(
                "FileAnalysis.review_regions must contain ReviewRegion values"
            )
        if not isinstance(self.fact_occurrences, tuple) or any(
            not isinstance(item, FactOccurrence) for item in self.fact_occurrences
        ):
            raise ValueError(
                "FileAnalysis.fact_occurrences must contain FactOccurrence values"
            )
        if self.schema_version != FILE_ANALYSIS_SCHEMA_VERSION:
            raise ValueError(
                f"FileAnalysis.schema_version must be {FILE_ANALYSIS_SCHEMA_VERSION!r}"
            )
        _non_empty(self.parser_version, "FileAnalysis.parser_version")
        if self.analysis_id != self.expected_id(
            self.source_ref.source_ref_id, self.parser_version
        ):
            raise ValueError("FileAnalysis.analysis_id does not match its inputs")
        _sorted_unique_strings(self.diagnostics, "FileAnalysis.diagnostics")
        unsupported_diagnostics = set(self.diagnostics) - _FILE_ANALYSIS_DIAGNOSTICS
        if unsupported_diagnostics:
            raise ValueError(
                "FileAnalysis.diagnostics contains unsupported codes: "
                f"{sorted(unsupported_diagnostics)!r}"
            )
        if self.parser_quality.layer != "L1" and (
            self.declarations or self.review_regions or self.fact_occurrences
        ):
            raise ValueError(
                "non-L1 FileAnalysis must not contain formal occurrence structures"
            )

        declarations = {item.declaration_id: item for item in self.declarations}
        regions = {item.region_id: item for item in self.review_regions}
        occurrences = {item.occurrence_id: item for item in self.fact_occurrences}
        if len(declarations) != len(self.declarations):
            raise ValueError("FileAnalysis has duplicate declaration_id values")
        if len(regions) != len(self.review_regions):
            raise ValueError("FileAnalysis has duplicate region_id values")
        if len(occurrences) != len(self.fact_occurrences):
            raise ValueError("FileAnalysis has duplicate occurrence_id values")

        declaration_order = tuple(
            sorted(
                self.declarations,
                key=lambda item: (
                    *_span_key(item.span, item.exact_range),
                    item.kind,
                    item.qualified_name,
                    item.declaration_id,
                ),
            )
        )
        if self.declarations != declaration_order:
            raise ValueError("FileAnalysis.declarations must use stable source order")
        region_order = tuple(
            sorted(
                self.review_regions,
                key=lambda item: (
                    *_span_key(item.span, item.exact_range),
                    item.kind,
                    item.symbol,
                    item.region_id,
                ),
            )
        )
        if self.review_regions != region_order:
            raise ValueError("FileAnalysis.review_regions must use stable source order")
        occurrence_order = tuple(
            sorted(
                self.fact_occurrences,
                key=lambda item: (
                    *_span_key(item.span, item.exact_range),
                    item.kind,
                    item.canonical_name or item.name,
                    item.occurrence_id,
                ),
            )
        )
        if self.fact_occurrences != occurrence_order:
            raise ValueError("FileAnalysis.fact_occurrences must use stable source order")

        for item in self.declarations:
            expected_id = item.expected_id(
                self.source_ref.source_ref_id,
                item.kind,
                item.qualified_name,
                item.span,
                item.exact_range,
            )
            if item.declaration_id != expected_id:
                raise ValueError("DeclarationOccurrence.declaration_id is not reproducible")
            if item.parent_id is not None:
                parent = declarations.get(item.parent_id)
                if (
                    parent is None
                    or parent is item
                    or not _strictly_contains(
                        parent.exact_range,
                        item.exact_range,
                    )
                ):
                    raise ValueError(
                        "DeclarationOccurrence.parent_id must name a strictly "
                        "containing declaration"
                    )

        for item in self.declarations:
            seen = {item.declaration_id}
            parent_id = item.parent_id
            while parent_id is not None:
                if parent_id in seen:
                    raise ValueError("DeclarationOccurrence parent graph contains a cycle")
                seen.add(parent_id)
                parent_id = declarations[parent_id].parent_id

        for region in self.review_regions:
            expected_id = region.expected_id(
                self.source_ref.source_ref_id,
                region.kind,
                region.symbol,
                region.span,
                region.exact_range,
                region.owner_declaration_id,
            )
            if region.region_id != expected_id:
                raise ValueError("ReviewRegion.region_id is not reproducible")
            if region.owner_declaration_id is not None:
                owner = declarations.get(region.owner_declaration_id)
                if owner is None or not owner.exact_range.contains(region.exact_range):
                    raise ValueError(
                        "ReviewRegion owner must name a containing declaration"
                    )

        for occurrence in self.fact_occurrences:
            expected_id = occurrence.expected_id(
                self.source_ref.source_ref_id,
                occurrence.kind,
                occurrence.name,
                occurrence.canonical_name,
                occurrence.exact_range,
                occurrence.owner_ref,
            )
            if occurrence.occurrence_id != expected_id:
                raise ValueError("FactOccurrence.occurrence_id is not reproducible")
            if occurrence.owner_ref is None:
                continue
            if occurrence.owner_ref.kind == "declaration":
                declaration_owner = declarations.get(occurrence.owner_ref.ref_id)
                if declaration_owner is None or not declaration_owner.exact_range.contains(
                    occurrence.exact_range
                ):
                    raise ValueError(
                        "FactOccurrence owner_ref must name a containing owner"
                    )
            else:
                region_owner = regions.get(occurrence.owner_ref.ref_id)
                if region_owner is None or not region_owner.exact_range.contains(
                    occurrence.exact_range
                ):
                    raise ValueError(
                        "FactOccurrence owner_ref must name a containing owner"
                    )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FileParseResult:
    analysis: FileAnalysis
    compatibility_facts: CodeFacts

    def __post_init__(self) -> None:
        if not isinstance(self.analysis, FileAnalysis):
            raise ValueError("FileParseResult.analysis must use FileAnalysis")
        if not isinstance(self.compatibility_facts, CodeFacts):
            raise ValueError(
                "FileParseResult.compatibility_facts must use CodeFacts"
            )
        self.analysis.validate()
        if self.compatibility_facts.path != self.analysis.source_ref.path:
            raise ValueError(
                "FileParseResult compatibility_facts.path must match source_ref.path"
            )
        if self.compatibility_facts.parser_layer != self.analysis.parser_quality.layer:
            raise ValueError("FileParseResult parser layers must match")
        expected_warnings = tuple(sorted(set(self.compatibility_facts.warnings)))
        if self.analysis.parser_quality.warnings != expected_warnings:
            raise ValueError(
                "FileParseResult parser warnings must match compatibility_facts"
            )
        expected_hints = ScopedFacts.from_code_facts(self.compatibility_facts)
        if self.analysis.file_hints != expected_hints:
            raise ValueError(
                "FileParseResult file_hints must match compatibility_facts"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis": self.analysis.to_dict(),
            "compatibility_facts": self.compatibility_facts.to_dict(),
        }


def _validate_span_pair(span: SourceSpan, exact_range: ExactRange, context: str) -> None:
    if not isinstance(span, SourceSpan):
        raise ValueError(f"{context}.span must use SourceSpan")
    if (
        not isinstance(span.start_line, int)
        or isinstance(span.start_line, bool)
        or span.start_line < 1
        or not isinstance(span.end_line, int)
        or isinstance(span.end_line, bool)
        or span.end_line < span.start_line
    ):
        raise ValueError(f"{context}.span must use 1-based inclusive lines")
    if (
        span.start_line != exact_range.start_line
        or span.end_line != exact_range.end_line
    ):
        raise ValueError(f"{context}.span and exact_range lines must match")


def _validate_quality(value: AnalysisQuality) -> None:
    if value not in {"exact", "recovered", "degraded", "unresolved"}:
        raise ValueError(f"unsupported analysis quality: {value}")


def _validate_structural_quality(value: StructuralQuality) -> None:
    if value not in {"exact", "recovered"}:
        raise ValueError(f"unsupported structural quality: {value}")


def _validate_provenance(value: FactProvenance) -> None:
    if value not in {"L0", "L1", "recovered"}:
        raise ValueError(f"unsupported fact provenance: {value}")


def _strictly_contains(owner: ExactRange, child: ExactRange) -> bool:
    return owner.contains(child) and (
        owner.start_offset_utf16 < child.start_offset_utf16
        or child.end_offset_utf16 < owner.end_offset_utf16
    )
