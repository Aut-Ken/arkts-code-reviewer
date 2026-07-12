from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_V2_DIAGNOSTIC_CODES,
    REVIEW_UNIT_V2_KINDS,
    REVIEW_UNIT_V2_SELECTION_REASONS,
    ReviewUnitDiagnosticCode,
    ReviewUnitKind,
    SelectionReason,
    declaration_unit_id,
    fallback_unit_id,
    normalize_review_path,
)

if TYPE_CHECKING:
    from arkts_code_reviewer.code_analysis.change_set import ChangeSet
    from arkts_code_reviewer.code_analysis.file_analysis_models import (
        CodeSourceRef,
        FileParseResult,
        OwnerRef,
        UnitFactScope,
    )
    from arkts_code_reviewer.feature_routing.models import FeatureRoutingResult

ParserLayer = Literal["L0", "L1", "parse_degraded"]
AnalysisMode = Literal["full", "diff"]
SourceRole = Literal["base", "head"]
DeclarationKind = Literal[
    "struct",
    "class",
    "function",
    "method",
    "build_method",
    "builder",
    "ui_block",
]
REVIEW_UNIT_BUILD_SCHEMA_VERSION = "review-unit-build-v1"
ANALYSIS_RESULT_SCHEMA_VERSION = "analysis-result-v1"


@dataclass(frozen=True)
class SourceSpan:
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0

    @property
    def line_count(self) -> int:
        return max(0, self.end_line - self.start_line + 1)

    def contains_line_range(self, start_line: int, end_line: int) -> bool:
        return self.start_line <= start_line and end_line <= self.end_line

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True)
class ImportInfo:
    module: str
    default_name: str | None = None
    namespace_name: str | None = None
    named: dict[str, str] = field(default_factory=dict)


@dataclass
class Declaration:
    kind: DeclarationKind
    name: str
    qualified_name: str
    span: SourceSpan
    parent_name: str | None = None
    text: str = ""
    declaration_id: str | None = None
    parent_id: str | None = None
    start_offset_utf16: int | None = None
    end_offset_utf16: int | None = None

    @property
    def line_count(self) -> int:
        return self.span.line_count


@dataclass
class CodeFacts:
    path: str
    imports: list[ImportInfo] = field(default_factory=list)
    components: set[str] = field(default_factory=set)
    apis: set[str] = field(default_factory=set)
    decorators: set[str] = field(default_factory=set)
    attributes: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    syntax: set[str] = field(default_factory=set)
    declarations: list[Declaration] = field(default_factory=list)
    parser_layer: ParserLayer = "L0"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "imports": [asdict(item) for item in self.imports],
            "components": sorted(self.components),
            "apis": sorted(self.apis),
            "decorators": sorted(self.decorators),
            "attributes": sorted(self.attributes),
            "symbols": sorted(self.symbols),
            "syntax": sorted(self.syntax),
            "declarations": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "qualified_name": item.qualified_name,
                    "span": asdict(item.span),
                    "parent_name": item.parent_name,
                    "text": item.text,
                }
                for item in self.declarations
            ],
            "parser_layer": self.parser_layer,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CodeFeatures:
    components: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for values, context in (
            (self.components, "components"),
            (self.decorators, "decorators"),
            (self.apis, "apis"),
            (self.tags, "tags"),
        ):
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise ValueError(f"CodeFeatures.{context} must contain strings")
            if values != sorted(set(values)):
                raise ValueError(f"CodeFeatures.{context} must be sorted and unique")
        from arkts_code_reviewer.feature_routing.config import (
            load_default_feature_config,
        )

        registered_tags = set(load_default_feature_config().tags_by_id)
        if not set(self.tags).issubset(registered_tags):
            raise ValueError("CodeFeatures.tags contains unregistered Tag IDs")

    @classmethod
    def from_facts(cls, facts: CodeFacts, tags: set[str]) -> CodeFeatures:
        return cls(
            components=sorted(facts.components),
            decorators=sorted(facts.decorators),
            apis=sorted(facts.apis),
            tags=sorted(tags),
        )


@dataclass(frozen=True)
class HostSummary:
    struct: str | None = None
    decorators: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    lifecycle: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewUnitSpan:
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start_line, int)
            or isinstance(self.start_line, bool)
            or self.start_line < 1
        ):
            raise ValueError("ReviewUnitSpan.start_line must be >= 1")
        if (
            not isinstance(self.end_line, int)
            or isinstance(self.end_line, bool)
            or self.end_line < self.start_line
        ):
            raise ValueError("ReviewUnitSpan.end_line must be >= start_line")

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True)
class ReviewUnitDiagnostic:
    code: ReviewUnitDiagnosticCode
    lines: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        try:
            normalized_lines = tuple(self.lines)
        except TypeError as exc:
            raise ValueError("ReviewUnitDiagnostic.lines must be iterable") from exc
        object.__setattr__(self, "lines", normalized_lines)
        if self.code not in REVIEW_UNIT_V2_DIAGNOSTIC_CODES:
            raise ValueError(f"unsupported ReviewUnit diagnostic code: {self.code}")
        if any(
            not isinstance(line, int) or isinstance(line, bool) or line < 1
            for line in self.lines
        ):
            raise ValueError("ReviewUnitDiagnostic.lines must be 1-based")
        if list(self.lines) != sorted(set(self.lines)):
            raise ValueError("ReviewUnitDiagnostic.lines must be sorted and unique")
        if self.code not in {
            "changed_lines_outside_context",
            "hunk_out_of_range",
        } and self.lines:
            raise ValueError(
                f"ReviewUnit diagnostic {self.code!r} must not carry line payloads"
            )


@dataclass
class ReviewUnit:
    file: str
    unit_symbol: str
    unit_ref: str
    full_text: str
    changed_lines: list[int] = field(default_factory=list)
    file_changed_lines: list[int] = field(default_factory=list)
    unit_changed_lines: list[int] = field(default_factory=list)
    host_summary: HostSummary = field(default_factory=HostSummary)
    context_degraded: bool = False
    unit_id: str = field(kw_only=True)
    unit_kind: ReviewUnitKind = field(kw_only=True)
    source_span: ReviewUnitSpan = field(kw_only=True)
    context_span: ReviewUnitSpan = field(kw_only=True)
    changed_new_lines: list[int] = field(kw_only=True)
    selection_reason: SelectionReason = field(kw_only=True)
    diagnostics: list[ReviewUnitDiagnostic] = field(kw_only=True)
    source_ref_id: str | None = field(default=None, kw_only=True)
    source_role: SourceRole | None = field(default=None, kw_only=True)
    change_atom_ids: list[str] = field(default_factory=list, kw_only=True)
    changed_old_lines: list[int] = field(default_factory=list, kw_only=True)
    owner_ref: OwnerRef | None = field(default=None, kw_only=True)
    identity_source_ref_id: str | None = field(default=None, kw_only=True)
    identity_start_offset_utf16: int | None = field(default=None, kw_only=True)
    identity_end_offset_utf16: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for value, name in (
            (self.file, "file"),
            (self.unit_symbol, "unit_symbol"),
            (self.unit_ref, "unit_ref"),
            (self.unit_id, "unit_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"ReviewUnit.{name} must be a non-empty string")
        if self.unit_ref != f"{self.unit_symbol}@{self.file}":
            raise ValueError("ReviewUnit.unit_ref must preserve qualified_name@path")
        if not isinstance(self.full_text, str):
            raise ValueError("ReviewUnit.full_text must be a string")
        if not isinstance(self.host_summary, HostSummary):
            raise ValueError("ReviewUnit.host_summary must use HostSummary")
        if self.unit_kind not in REVIEW_UNIT_V2_KINDS:
            raise ValueError(f"unsupported ReviewUnit kind: {self.unit_kind}")
        if self.selection_reason not in REVIEW_UNIT_V2_SELECTION_REASONS:
            raise ValueError(
                f"unsupported ReviewUnit selection reason: {self.selection_reason}"
            )
        if not isinstance(self.context_degraded, bool):
            raise ValueError("ReviewUnit.context_degraded must be a boolean")
        if self.source_ref_id is not None and (
            not isinstance(self.source_ref_id, str) or not self.source_ref_id
        ):
            raise ValueError("ReviewUnit.source_ref_id must be non-empty or None")
        if self.source_role not in {None, "base", "head"}:
            raise ValueError("ReviewUnit.source_role must be base, head, or None")
        if self.source_role is None and (
            self.change_atom_ids or self.changed_old_lines
        ):
            raise ValueError(
                "legacy ReviewUnit cannot carry ChangeSet assignment fields"
            )
        if self.source_role is not None and self.source_ref_id is None:
            raise ValueError("ChangeSet ReviewUnit requires source_ref_id")
        if not isinstance(self.change_atom_ids, list) or any(
            not isinstance(atom_id, str)
            or not atom_id.startswith("change-atom:sha256:")
            for atom_id in self.change_atom_ids
        ):
            raise ValueError(
                "ReviewUnit.change_atom_ids must contain ChangeAtom identities"
            )
        if self.change_atom_ids != sorted(set(self.change_atom_ids)):
            raise ValueError("ReviewUnit.change_atom_ids must be sorted and unique")
        if self.source_role is not None and not self.change_atom_ids:
            raise ValueError("changed ReviewUnit requires at least one change_atom_id")
        if self.owner_ref is not None:
            from arkts_code_reviewer.code_analysis.file_analysis_models import OwnerRef

            if not isinstance(self.owner_ref, OwnerRef):
                raise ValueError("ReviewUnit.owner_ref must use OwnerRef or None")
            if self.source_ref_id is None:
                raise ValueError("owned ReviewUnit requires source_ref_id")
            expected_owner_kind = (
                "region"
                if self.unit_kind in {"field_region", "import_region"}
                else "declaration"
            )
            if self.unit_kind != "fallback" and self.owner_ref.kind != expected_owner_kind:
                raise ValueError(
                    "ReviewUnit owner kind must match its declaration/region kind"
                )
        if self.unit_kind in {"field_region", "import_region"} and self.owner_ref is None:
            raise ValueError("ReviewRegion ReviewUnit requires owner_ref")
        if self.unit_kind == "fallback" and self.owner_ref is not None:
            raise ValueError("fallback ReviewUnit must not carry owner_ref")
        if (
            self.source_role is not None
            and self.unit_kind != "fallback"
            and self.owner_ref is None
        ):
            raise ValueError("ChangeSet declaration ReviewUnit requires owner_ref")
        if self.identity_source_ref_id is not None:
            if self.identity_source_ref_id != self.source_ref_id:
                raise ValueError(
                    "ReviewUnit identity source must match source_ref_id"
                )
            if self.source_role is None:
                raise ValueError(
                    "source-scoped ReviewUnit identity requires a source role"
                )
        elif self.source_role is not None:
            raise ValueError(
                "ChangeSet ReviewUnit identity must include its immutable source"
            )
        offset_values = (
            self.identity_start_offset_utf16,
            self.identity_end_offset_utf16,
        )
        if (offset_values[0] is None) != (offset_values[1] is None):
            raise ValueError("ReviewUnit identity offsets must be provided together")
        if offset_values[0] is not None and (
            not isinstance(offset_values[0], int)
            or isinstance(offset_values[0], bool)
            or offset_values[0] < 0
            or not isinstance(offset_values[1], int)
            or isinstance(offset_values[1], bool)
            or offset_values[1] <= offset_values[0]
        ):
            raise ValueError("ReviewUnit identity offsets must be a valid UTF-16 range")
        if not isinstance(self.source_span, ReviewUnitSpan) or not isinstance(
            self.context_span, ReviewUnitSpan
        ):
            raise ValueError("ReviewUnit spans must use ReviewUnitSpan")
        if not (
            self.context_span.start_line <= self.source_span.start_line
            and self.source_span.end_line <= self.context_span.end_line
        ):
            raise ValueError("ReviewUnit.source_span must be inside context_span")
        if (self.unit_kind == "fallback") != (
            self.selection_reason == "fallback_window"
        ):
            raise ValueError("ReviewUnit fallback kind and reason must be used together")
        if self.unit_kind == "fallback" and not self.context_degraded:
            raise ValueError("fallback ReviewUnit must be context_degraded")
        if self.unit_kind == "fallback" and self.unit_symbol != (
            f"hunk-L{self.source_span.start_line}-L{self.source_span.end_line}"
        ):
            raise ValueError("fallback ReviewUnit symbol must identify its source span")

        expected_id = (
            fallback_unit_id(
                self.file,
                self.source_span.start_line,
                self.source_span.end_line,
                self.context_span.start_line,
                self.context_span.end_line,
                source_role=self.source_role,
                source_ref_id=self.identity_source_ref_id,
            )
            if self.unit_kind == "fallback"
            else declaration_unit_id(
                self.file,
                self.unit_kind,
                self.unit_symbol,
                self.source_span.start_line,
                self.source_span.end_line,
                start_offset_utf16=self.identity_start_offset_utf16,
                end_offset_utf16=self.identity_end_offset_utf16,
                source_role=self.source_role,
                source_ref_id=self.identity_source_ref_id,
            )
        )
        if self.unit_id != expected_id:
            raise ValueError("ReviewUnit.unit_id does not match its identity fields")

        for values, name in (
            (self.changed_lines, "changed_lines"),
            (self.file_changed_lines, "file_changed_lines"),
            (self.unit_changed_lines, "unit_changed_lines"),
            (self.changed_new_lines, "changed_new_lines"),
            (self.changed_old_lines, "changed_old_lines"),
        ):
            if not isinstance(values, list) or any(
                not isinstance(line, int) or isinstance(line, bool) or line < 1
                for line in values
            ):
                raise ValueError(f"ReviewUnit.{name} must contain 1-based integer lines")
            if values != sorted(set(values)):
                raise ValueError(f"ReviewUnit.{name} must be sorted and unique")
        if self.changed_lines != self.file_changed_lines:
            raise ValueError("ReviewUnit changed_lines compatibility fields must match")

        projected_changed_lines = [
            line
            for line in self.file_changed_lines
            if self.context_span.contains_line(line)
        ]
        if self.source_role == "base":
            if self.changed_new_lines:
                raise ValueError("base ReviewUnit must not carry changed_new_lines")
            if self.changed_old_lines != projected_changed_lines:
                raise ValueError(
                    "ReviewUnit.changed_old_lines must project base file lines into context"
                )
            effective_changed_lines = self.changed_old_lines
        else:
            if self.changed_old_lines:
                raise ValueError("head/legacy ReviewUnit must not carry changed_old_lines")
            if self.changed_new_lines != projected_changed_lines:
                raise ValueError(
                    "ReviewUnit.changed_new_lines must project file_changed_lines into context"
                )
            effective_changed_lines = self.changed_new_lines
        expected_unit_changed_lines = [
            line - self.context_span.start_line + 1
            for line in effective_changed_lines
        ]
        if self.unit_changed_lines != expected_unit_changed_lines:
            raise ValueError(
                "ReviewUnit.unit_changed_lines must be relative to context_span"
            )

        if not isinstance(self.diagnostics, list) or any(
            not isinstance(item, ReviewUnitDiagnostic) for item in self.diagnostics
        ):
            raise ValueError("ReviewUnit.diagnostics must contain structured diagnostics")
        diagnostic_keys = [(item.code, item.lines) for item in self.diagnostics]
        if diagnostic_keys != sorted(set(diagnostic_keys)):
            raise ValueError("ReviewUnit.diagnostics must be sorted and unique")
        if len({item.code for item in self.diagnostics}) != len(self.diagnostics):
            raise ValueError("ReviewUnit diagnostics must contain each code at most once")

        diagnostics_by_code = {item.code: item for item in self.diagnostics}
        parser_quality_codes = {
            "parser_degraded",
            "parser_error_nodes",
            "parser_missing_nodes",
        }
        if parser_quality_codes.intersection(diagnostics_by_code) and not (
            self.context_degraded
        ):
            raise ValueError(
                "ReviewUnit parser quality diagnostics require context_degraded"
            )
        outside_lines = tuple(
            line
            for line in self.file_changed_lines
            if not self.context_span.contains_line(line)
        )
        outside_diagnostic = diagnostics_by_code.get("changed_lines_outside_context")
        if outside_lines != (
            outside_diagnostic.lines if outside_diagnostic is not None else ()
        ):
            raise ValueError(
                "ReviewUnit outside changed lines must match their diagnostic"
            )
        has_no_match = "no_matching_declaration" in diagnostics_by_code
        if (self.unit_kind == "fallback") != has_no_match:
            raise ValueError(
                "fallback ReviewUnit must carry exactly one no_matching_declaration code"
            )


def _review_unit_sort_key(unit: ReviewUnit) -> tuple[int, int, int, int, str]:
    return (
        unit.context_span.start_line,
        unit.context_span.end_line,
        unit.source_span.start_line,
        unit.source_span.end_line,
        unit.unit_id,
    )


def _validate_review_unit_diagnostics(
    diagnostics: list[ReviewUnitDiagnostic],
    context: str,
) -> None:
    if not isinstance(diagnostics, list) or any(
        not isinstance(item, ReviewUnitDiagnostic) for item in diagnostics
    ):
        raise ValueError(f"{context} must contain structured diagnostics")
    diagnostic_keys = [(item.code, item.lines) for item in diagnostics]
    if diagnostic_keys != sorted(set(diagnostic_keys)):
        raise ValueError(f"{context} must be sorted and unique")
    if len({item.code for item in diagnostics}) != len(diagnostics):
        raise ValueError(f"{context} must contain each code at most once")


@dataclass(frozen=True)
class ParserQuality:
    """Parser quality retained at the file boundary used to build ReviewUnits."""

    parser_layer: ParserLayer
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.parser_layer not in {"L0", "L1", "parse_degraded"}:
            raise ValueError(f"unsupported parser layer: {self.parser_layer}")
        if not isinstance(self.warnings, list) or any(
            not isinstance(warning, str) or not warning for warning in self.warnings
        ):
            raise ValueError("ParserQuality.warnings must contain non-empty strings")
        if self.warnings != sorted(set(self.warnings)):
            raise ValueError("ParserQuality.warnings must be sorted and unique")


@dataclass
class ReviewUnitFileResult:
    """ReviewUnit output and file-scoped quality/assignment diagnostics."""

    path: str
    units: list[ReviewUnit]
    parser_quality: ParserQuality
    diagnostics: list[ReviewUnitDiagnostic] = field(default_factory=list)
    unassigned_hunk_lines: list[int] = field(default_factory=list)
    source_ref_id: str | None = None
    source_role: SourceRole | None = None
    changed_file_id: str | None = None
    unassigned_change_atom_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        normalized_path = normalize_review_path(self.path)
        if self.source_ref_id is not None and (
            not isinstance(self.source_ref_id, str) or not self.source_ref_id
        ):
            raise ValueError("ReviewUnitFileResult.source_ref_id must be non-empty or None")
        if self.source_role not in {None, "base", "head"}:
            raise ValueError(
                "ReviewUnitFileResult.source_role must be base, head, or None"
            )
        if (self.source_role is None) != (self.changed_file_id is None):
            raise ValueError(
                "ReviewUnitFileResult source_role and changed_file_id must be set together"
            )
        if self.changed_file_id is not None and (
            not isinstance(self.changed_file_id, str)
            or not self.changed_file_id.startswith("changed-file:sha256:")
        ):
            raise ValueError(
                "ReviewUnitFileResult.changed_file_id must use a ChangedFile identity"
            )
        if self.source_role is not None and self.source_ref_id is None:
            raise ValueError("ChangeSet file result requires source_ref_id")
        if not isinstance(self.units, list) or any(
            not isinstance(unit, ReviewUnit) for unit in self.units
        ):
            raise ValueError("ReviewUnitFileResult.units must contain ReviewUnit values")
        for unit in self.units:
            unit.validate()
            if normalize_review_path(unit.file) != normalized_path:
                raise ValueError(
                    "ReviewUnitFileResult units must belong to the result path"
                )
            if self.source_ref_id is not None and unit.source_ref_id != self.source_ref_id:
                raise ValueError(
                    "ReviewUnitFileResult units must use the result source_ref_id"
                )
            if self.source_role is not None and unit.source_role != self.source_role:
                raise ValueError(
                    "ReviewUnitFileResult units must use the result source_role"
                )
        if self.units != sorted(self.units, key=_review_unit_sort_key):
            raise ValueError("ReviewUnitFileResult.units must use stable source order")
        unit_ids = [unit.unit_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("ReviewUnitFileResult.units must have unique unit_id values")

        if not isinstance(self.parser_quality, ParserQuality):
            raise ValueError(
                "ReviewUnitFileResult.parser_quality must use ParserQuality"
            )
        self.parser_quality.validate()
        required_quality_codes: set[str] = set()
        if self.parser_quality.parser_layer == "parse_degraded":
            required_quality_codes.add("parser_degraded")
        for warning in self.parser_quality.warnings:
            warning_code = warning.partition(":")[0]
            if warning_code in {
                "arkts_tree_sitter_error_nodes",
                "tree_sitter_error_nodes",
            }:
                required_quality_codes.add("parser_error_nodes")
            elif warning_code in {
                "arkts_tree_sitter_missing_nodes",
                "tree_sitter_missing_nodes",
            }:
                required_quality_codes.add("parser_missing_nodes")
        for unit in self.units:
            unit_quality_codes = {
                diagnostic.code for diagnostic in unit.diagnostics
            }
            if not required_quality_codes.issubset(unit_quality_codes):
                raise ValueError(
                    "ReviewUnitFileResult parser quality must propagate to every Unit"
                )
        _validate_review_unit_diagnostics(
            self.diagnostics,
            "ReviewUnitFileResult.diagnostics",
        )

        if not isinstance(self.unassigned_hunk_lines, list) or any(
            not isinstance(line, int) or isinstance(line, bool) or line < 1
            for line in self.unassigned_hunk_lines
        ):
            raise ValueError(
                "ReviewUnitFileResult.unassigned_hunk_lines must contain 1-based lines"
            )
        if self.unassigned_hunk_lines != sorted(set(self.unassigned_hunk_lines)):
            raise ValueError(
                "ReviewUnitFileResult.unassigned_hunk_lines must be sorted and unique"
            )
        assigned_lines = {
            line for unit in self.units for line in unit.changed_new_lines
        }
        if assigned_lines.intersection(self.unassigned_hunk_lines):
            raise ValueError(
                "ReviewUnitFileResult unassigned lines must not be assigned to a Unit"
            )
        if not isinstance(self.unassigned_change_atom_ids, list) or any(
            not isinstance(atom_id, str)
            or not atom_id.startswith("change-atom:sha256:")
            for atom_id in self.unassigned_change_atom_ids
        ):
            raise ValueError(
                "ReviewUnitFileResult.unassigned_change_atom_ids must contain atom IDs"
            )
        if self.unassigned_change_atom_ids != sorted(
            set(self.unassigned_change_atom_ids)
        ):
            raise ValueError(
                "ReviewUnitFileResult.unassigned_change_atom_ids must be sorted and unique"
            )
        assigned_atom_ids = {
            atom_id for unit in self.units for atom_id in unit.change_atom_ids
        }
        if assigned_atom_ids.intersection(self.unassigned_change_atom_ids):
            raise ValueError(
                "ReviewUnitFileResult atom assignments cannot also be unassigned"
            )
        if self.source_role is not None:
            has_unassigned_diagnostic = any(
                item.code == "change_atom_unassigned" for item in self.diagnostics
            )
            if bool(self.unassigned_change_atom_ids) != has_unassigned_diagnostic:
                raise ValueError(
                    "source-role file result unassigned atoms require a diagnostic"
                )


@dataclass
class ReviewUnitBuildResult:
    """Deterministic batch envelope for ReviewUnit construction."""

    schema_version: str
    mode: AnalysisMode
    file_results: list[ReviewUnitFileResult]
    diagnostics: list[ReviewUnitDiagnostic] = field(default_factory=list)
    change_set_id: str | None = None
    unassigned_change_atom_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version not in {
            REVIEW_UNIT_BUILD_SCHEMA_VERSION,
            "review-unit-build-v2",
            "review-unit-build-v3",
        }:
            raise ValueError(
                "ReviewUnitBuildResult.schema_version is unsupported"
            )
        if self.mode not in {"full", "diff"}:
            raise ValueError(f"unsupported ReviewUnit analysis mode: {self.mode}")
        if not isinstance(self.file_results, list) or any(
            not isinstance(result, ReviewUnitFileResult)
            for result in self.file_results
        ):
            raise ValueError(
                "ReviewUnitBuildResult.file_results must contain file results"
            )
        for result in self.file_results:
            result.validate()
            if self.schema_version == "review-unit-build-v2" and result.source_ref_id is None:
                raise ValueError(
                    "review-unit-build-v2 file results require source_ref_id"
                )
            if self.schema_version == "review-unit-build-v3" and (
                result.source_ref_id is None
                or result.source_role is None
                or result.changed_file_id is None
            ):
                raise ValueError(
                    "review-unit-build-v3 file results require source identity, role, "
                    "and changed_file_id"
                )
            if self.mode == "full" and result.unassigned_hunk_lines:
                raise ValueError(
                    "full ReviewUnitBuildResult must not contain unassigned hunk lines"
                )
        normalized_paths = [normalize_review_path(result.path) for result in self.file_results]
        if self.schema_version == "review-unit-build-v3":
            if self.mode != "diff":
                raise ValueError("review-unit-build-v3 mode must be diff")
            if not isinstance(self.change_set_id, str) or not self.change_set_id.startswith(
                "change-set:sha256:"
            ):
                raise ValueError(
                    "review-unit-build-v3 requires a deterministic change_set_id"
                )
            result_keys = [
                (
                    result.changed_file_id,
                    0 if result.source_role == "base" else 1,
                    normalize_review_path(result.path),
                    result.source_ref_id,
                )
                for result in self.file_results
            ]
            if result_keys != sorted(result_keys):
                raise ValueError(
                    "review-unit-build-v3 file results must use changed-file/source order"
                )
            identity_keys = [
                (result.path, result.source_role, result.source_ref_id)
                for result in self.file_results
            ]
            if len(identity_keys) != len(set(identity_keys)):
                raise ValueError(
                    "review-unit-build-v3 file results require unique source-role identities"
                )
        else:
            if self.change_set_id is not None or self.unassigned_change_atom_ids:
                raise ValueError(
                    "legacy ReviewUnit build results cannot carry ChangeSet fields"
                )
            if any(
                result.source_role is not None
                or result.changed_file_id is not None
                or result.unassigned_change_atom_ids
                or any(unit.source_role is not None for unit in result.units)
                for result in self.file_results
            ):
                raise ValueError(
                    "legacy ReviewUnit build results cannot carry ChangeSet fields"
                )
            if normalized_paths != sorted(normalized_paths):
                raise ValueError(
                    "ReviewUnitBuildResult.file_results must use stable path order"
                )
            if len(normalized_paths) != len(set(normalized_paths)):
                raise ValueError(
                    "ReviewUnitBuildResult.file_results must have unique paths"
                )
        _validate_review_unit_diagnostics(
            self.diagnostics,
            "ReviewUnitBuildResult.diagnostics",
        )

        if not isinstance(self.unassigned_change_atom_ids, list) or any(
            not isinstance(atom_id, str)
            or not atom_id.startswith("change-atom:sha256:")
            for atom_id in self.unassigned_change_atom_ids
        ):
            raise ValueError(
                "ReviewUnitBuildResult.unassigned_change_atom_ids must contain atom IDs"
            )
        if self.unassigned_change_atom_ids != sorted(
            set(self.unassigned_change_atom_ids)
        ):
            raise ValueError(
                "ReviewUnitBuildResult.unassigned_change_atom_ids must be sorted and unique"
            )
        if self.schema_version == "review-unit-build-v3":
            file_unassigned = {
                atom_id
                for result in self.file_results
                for atom_id in result.unassigned_change_atom_ids
            }
            if set(self.unassigned_change_atom_ids) != file_unassigned:
                raise ValueError(
                    "ReviewUnitBuildResult unassigned atoms must aggregate file results"
                )
            has_unassigned_diagnostic = any(
                item.code == "change_atom_unassigned" for item in self.diagnostics
            )
            if bool(self.unassigned_change_atom_ids) != has_unassigned_diagnostic:
                raise ValueError(
                    "unassigned ChangeAtoms require exactly one build diagnostic"
                )

        unit_ids = [unit.unit_id for unit in self._flatten_units_unchecked()]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError(
                "ReviewUnitBuildResult units must have globally unique unit_id values"
            )

    def flatten_units(self) -> list[ReviewUnit]:
        """Return Units in validated file/source order for legacy consumers."""

        self.validate()
        return self._flatten_units_unchecked()

    def _flatten_units_unchecked(self) -> list[ReviewUnit]:
        return [unit for result in self.file_results for unit in result.units]


def _validate_change_set_build_graph(
    change_set: ChangeSet,
    build_result: ReviewUnitBuildResult,
) -> None:
    """Cross-check every v3 result edge against its immutable ChangeSet."""

    changed_files = {item.changed_file_id: item for item in change_set.files}
    sources = {item.source_ref_id: item for item in change_set.source_refs}
    atoms = {item.atom_id: item for item in change_set.atoms}

    expected_results: set[tuple[str, SourceRole, str, str]] = set()
    for changed_file in change_set.files:
        role_sources: tuple[tuple[SourceRole, str | None], ...] = (
            ("base", changed_file.old_source_ref_id),
            ("head", changed_file.new_source_ref_id),
        )
        for role, source_ref_id in role_sources:
            if source_ref_id is None:
                continue
            source_ref = sources[source_ref_id]
            expected_results.add(
                (changed_file.changed_file_id, role, source_ref_id, source_ref.path)
            )
    actual_results = {
        (
            result.changed_file_id,
            result.source_role,
            result.source_ref_id,
            normalize_review_path(result.path),
        )
        for result in build_result.file_results
    }
    if actual_results != expected_results:
        raise ValueError(
            "review-unit-build-v3 file results do not match ChangeSet sources"
        )

    for result in build_result.file_results:
        if (
            result.changed_file_id is None
            or result.source_role is None
            or result.source_ref_id is None
        ):
            raise ValueError(
                "review-unit-build-v3 file result lacks source identity"
            )
        changed_file = changed_files[result.changed_file_id]
        allowed_atom_ids = {
            atom_id
            for atom_id in changed_file.atom_ids
            if (
                atoms[atom_id].old_source_ref_id
                if result.source_role == "base"
                else atoms[atom_id].new_source_ref_id
            )
            == result.source_ref_id
        }
        if not set(result.unassigned_change_atom_ids).issubset(allowed_atom_ids):
            raise ValueError(
                "ReviewUnitFileResult contains an unrelated unassigned ChangeAtom"
            )

        line_to_atom: dict[int, str] = {}
        expected_lines_by_atom: dict[str, set[int]] = {}
        for atom_id in allowed_atom_ids:
            atom = atoms[atom_id]
            lines = set(
                atom.deleted_old_lines
                if result.source_role == "base"
                else atom.added_new_lines
            )
            expected_lines_by_atom[atom_id] = lines
            for line in lines:
                if line in line_to_atom:
                    raise ValueError(
                        "ChangeSet changed line belongs to multiple ChangeAtoms"
                    )
                line_to_atom[line] = atom_id

        assigned_lines_by_atom: dict[str, set[int]] = {
            atom_id: set() for atom_id in allowed_atom_ids
        }
        for unit in result.units:
            active_lines = (
                unit.changed_old_lines
                if result.source_role == "base"
                else unit.changed_new_lines
            )
            try:
                mapped_atom_ids = sorted({line_to_atom[line] for line in active_lines})
            except KeyError as exc:
                raise ValueError(
                    "ReviewUnit changed line is not present in its ChangeSet side"
                ) from exc
            if unit.change_atom_ids != mapped_atom_ids:
                raise ValueError(
                    "ReviewUnit change_atom_ids do not match its changed lines"
                )
            for line in active_lines:
                assigned_lines_by_atom[line_to_atom[line]].add(line)

        unassigned = set(result.unassigned_change_atom_ids)
        for atom_id, expected_lines in expected_lines_by_atom.items():
            assigned_lines = assigned_lines_by_atom[atom_id]
            if atom_id in unassigned:
                if assigned_lines:
                    raise ValueError(
                        "unassigned ChangeAtom must not retain partial Unit coverage"
                    )
            elif assigned_lines != expected_lines:
                raise ValueError(
                    "ReviewUnit file result does not exactly cover its ChangeAtom side"
                )

    has_binary_file = any(item.is_binary for item in change_set.files)
    has_binary_diagnostic = any(
        item.code == "binary_change_unsupported"
        for item in build_result.diagnostics
    )
    if has_binary_file != has_binary_diagnostic:
        raise ValueError(
            "binary ChangeSet files require a build-level unsupported diagnostic"
        )


@dataclass(frozen=True)
class RetrievalUnit:
    unit_ref: str
    code_features: CodeFeatures
    intent_summary: str
    unit_id: str | None = field(default=None, kw_only=True)
    source_ref_id: str | None = field(default=None, kw_only=True)
    unit_fact_scope: UnitFactScope | None = field(default=None, kw_only=True)
    dimensions: list[str] = field(default_factory=list, kw_only=True)
    routing_tags: list[str] = field(default_factory=list, kw_only=True)

    def __post_init__(self) -> None:
        for required_value, name in (
            (self.unit_ref, "unit_ref"),
            (self.intent_summary, "intent_summary"),
        ):
            if not isinstance(required_value, str) or not required_value:
                raise ValueError(f"RetrievalUnit.{name} must be a non-empty string")
        for optional_value, name in (
            (self.unit_id, "unit_id"),
            (self.source_ref_id, "source_ref_id"),
        ):
            if optional_value is not None and (
                not isinstance(optional_value, str) or not optional_value
            ):
                raise ValueError(
                    f"RetrievalUnit.{name} must be non-empty or None"
                )
        for values, name in (
            (self.dimensions, "dimensions"),
            (self.routing_tags, "routing_tags"),
        ):
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise ValueError(f"RetrievalUnit.{name} must contain strings")
            if values != sorted(set(values)):
                raise ValueError(
                    f"RetrievalUnit.{name} must be sorted and unique"
                )
        from arkts_code_reviewer.feature_routing.config import (
            load_default_feature_config,
        )

        feature_config = load_default_feature_config()
        if not set(self.dimensions).issubset(feature_config.dimensions_by_id):
            raise ValueError("RetrievalUnit.dimensions contains unregistered IDs")
        if not set(self.routing_tags).issubset(feature_config.tags_by_id):
            raise ValueError("RetrievalUnit.routing_tags contains unregistered IDs")
        if self.unit_fact_scope is not None:
            from arkts_code_reviewer.code_analysis.file_analysis_models import (
                UnitFactScope,
            )

            if not isinstance(self.unit_fact_scope, UnitFactScope):
                raise ValueError(
                    "RetrievalUnit.unit_fact_scope must use UnitFactScope or None"
                )
            if self.unit_id != self.unit_fact_scope.unit_id:
                raise ValueError(
                    "RetrievalUnit.unit_id must match its UnitFactScope"
                )
            if self.source_ref_id != self.unit_fact_scope.source_ref_id:
                raise ValueError(
                    "RetrievalUnit.source_ref_id must match its UnitFactScope"
                )
            exact = self.unit_fact_scope.unit_exact
            if self.code_features.components != list(exact.components):
                raise ValueError(
                    "RetrievalUnit components must come from unit_exact"
                )
            if self.code_features.decorators != list(exact.decorators):
                raise ValueError(
                    "RetrievalUnit decorators must come from unit_exact"
                )
            if self.code_features.apis != list(exact.apis):
                raise ValueError("RetrievalUnit APIs must come from unit_exact")


@dataclass(frozen=True)
class MrContext:
    triggered_dimensions: list[str]
    token_budget: int

    def __post_init__(self) -> None:
        if not isinstance(self.triggered_dimensions, list) or any(
            not isinstance(value, str) or not value
            for value in self.triggered_dimensions
        ):
            raise ValueError("MrContext.triggered_dimensions must contain strings")
        if self.triggered_dimensions != sorted(set(self.triggered_dimensions)):
            raise ValueError(
                "MrContext.triggered_dimensions must be sorted and unique"
            )
        if (
            not isinstance(self.token_budget, int)
            or isinstance(self.token_budget, bool)
            or self.token_budget < 1
        ):
            raise ValueError("MrContext.token_budget must be an integer >= 1")
        from arkts_code_reviewer.feature_routing.config import (
            load_default_feature_config,
        )

        if not set(self.triggered_dimensions).issubset(
            load_default_feature_config().dimensions_by_id
        ):
            raise ValueError("MrContext contains unregistered Dimension IDs")


@dataclass(frozen=True)
class RetrievalQuery:
    mr_context: MrContext
    units: list[RetrievalUnit]

    def __post_init__(self) -> None:
        if not isinstance(self.mr_context, MrContext):
            raise ValueError("RetrievalQuery.mr_context must use MrContext")
        if not isinstance(self.units, list) or any(
            not isinstance(unit, RetrievalUnit) for unit in self.units
        ):
            raise ValueError("RetrievalQuery.units must contain RetrievalUnit values")


@dataclass
class AnalysisMetadata:
    parser_layer: ParserLayer
    warnings: list[str] = field(default_factory=list)
    whitelist_version: str | None = None


@dataclass
class AnalysisResult:
    retrieval_query: RetrievalQuery
    review_units: list[ReviewUnit]
    metadata: AnalysisMetadata
    review_unit_build_result: ReviewUnitBuildResult | None = field(
        default=None,
        kw_only=True,
    )
    file_parse_results: list[FileParseResult] = field(
        default_factory=list,
        kw_only=True,
        repr=False,
    )
    unit_fact_scopes: list[UnitFactScope] = field(
        default_factory=list,
        kw_only=True,
    )
    change_set: ChangeSet | None = field(default=None, kw_only=True)
    feature_routing_result: FeatureRoutingResult = field(kw_only=True)
    schema_version: str = field(
        default=ANALYSIS_RESULT_SCHEMA_VERSION,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != ANALYSIS_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"AnalysisResult.schema_version must be "
                f"{ANALYSIS_RESULT_SCHEMA_VERSION!r}"
            )
        if not isinstance(self.review_units, list) or any(
            not isinstance(unit, ReviewUnit) for unit in self.review_units
        ):
            raise ValueError("AnalysisResult.review_units must contain ReviewUnit values")
        if self.review_unit_build_result is not None:
            if not isinstance(self.review_unit_build_result, ReviewUnitBuildResult):
                raise ValueError(
                    "AnalysisResult.review_unit_build_result must use ReviewUnitBuildResult"
                )
            flattened_units = self.review_unit_build_result.flatten_units()
            if self.review_units != flattened_units:
                raise ValueError(
                    "AnalysisResult.review_units must match "
                    "ReviewUnitBuildResult.flatten_units()"
                )

        from arkts_code_reviewer.code_analysis.file_analysis_models import (
            FileParseResult,
            UnitFactScope,
        )

        if not isinstance(self.file_parse_results, list) or any(
            not isinstance(result, FileParseResult)
            for result in self.file_parse_results
        ):
            raise ValueError(
                "AnalysisResult.file_parse_results must contain FileParseResult values"
            )
        parse_keys = [
            (
                result.analysis.source_ref.path,
                result.analysis.source_ref.revision,
                result.analysis.source_ref.source_ref_id,
            )
            for result in self.file_parse_results
        ]
        parse_source_ids = [key[2] for key in parse_keys]
        if parse_keys != sorted(parse_keys) or len(parse_source_ids) != len(
            set(parse_source_ids)
        ):
            raise ValueError(
                "AnalysisResult.file_parse_results must use unique stable source order"
            )
        if not isinstance(self.unit_fact_scopes, list) or any(
            not isinstance(scope, UnitFactScope) for scope in self.unit_fact_scopes
        ):
            raise ValueError(
                "AnalysisResult.unit_fact_scopes must contain UnitFactScope values"
            )
        scope_ids = [scope.unit_id for scope in self.unit_fact_scopes]
        if (self.file_parse_results or self.unit_fact_scopes) and scope_ids != [
            unit.unit_id for unit in self.review_units
        ]:
            raise ValueError(
                "AnalysisResult.unit_fact_scopes must align with review_units"
            )
        if self.file_parse_results or self.unit_fact_scopes:
            retrieval_units = self.retrieval_query.units
            if [item.unit_id for item in retrieval_units] != scope_ids:
                raise ValueError(
                    "AnalysisResult RetrievalUnits must align by unit_id"
                )
            for unit, scope, retrieval_unit in zip(
                self.review_units,
                self.unit_fact_scopes,
                retrieval_units,
                strict=True,
            ):
                if (
                    scope.source_ref_id != unit.source_ref_id
                    or retrieval_unit.source_ref_id != unit.source_ref_id
                ):
                    raise ValueError(
                        "AnalysisResult Unit scopes must align by source_ref_id"
                    )
                if retrieval_unit.unit_ref != unit.unit_ref:
                    raise ValueError(
                        "AnalysisResult RetrievalUnits must align by unit_ref"
                    )
                if retrieval_unit.unit_fact_scope != scope:
                    raise ValueError(
                        "AnalysisResult RetrievalUnits must retain their UnitFactScope"
                    )
            from arkts_code_reviewer.code_analysis.unit_facts import project

            parse_results_by_source = {
                item.analysis.source_ref.source_ref_id: item
                for item in self.file_parse_results
            }
            for unit, scope in zip(
                self.review_units,
                self.unit_fact_scopes,
                strict=True,
            ):
                if unit.source_ref_id is None:
                    raise ValueError(
                        "occurrence-scoped ReviewUnit requires source_ref_id"
                    )
                parse_result = parse_results_by_source.get(unit.source_ref_id)
                if parse_result is None:
                    raise ValueError(
                        "ReviewUnit source_ref_id has no FileParseResult"
                    )
                if scope != project(parse_result.analysis, unit):
                    raise ValueError(
                        "AnalysisResult UnitFactScope must equal occurrence projection"
                    )
        from arkts_code_reviewer.feature_routing.models import FeatureRoutingResult

        if not isinstance(self.feature_routing_result, FeatureRoutingResult):
            raise ValueError(
                "AnalysisResult.feature_routing_result must use FeatureRoutingResult"
            )
        self.feature_routing_result.validate_replay(self.unit_fact_scopes)
        profiles_by_unit = {
            profile.unit_id: profile
            for profile in self.feature_routing_result.units
        }
        if set(profiles_by_unit) != set(scope_ids):
            raise ValueError(
                "AnalysisResult FeatureRoutingResult must cover every UnitFactScope"
            )
        for retrieval_unit in self.retrieval_query.units:
            if retrieval_unit.unit_id is None:
                raise ValueError(
                    "Feature-routed RetrievalUnit requires unit_id"
                )
            profile = profiles_by_unit.get(retrieval_unit.unit_id)
            if profile is None:
                raise ValueError(
                    "Feature-routed RetrievalUnit has no UnitFeatureProfile"
                )
            if (
                retrieval_unit.code_features.tags != list(profile.exact_tags)
                or retrieval_unit.dimensions != list(profile.dimensions)
                or retrieval_unit.routing_tags != list(profile.routing_tags)
            ):
                raise ValueError(
                    "RetrievalUnit compatibility view must match FeatureRoutingResult"
                )
        if self.retrieval_query.mr_context.triggered_dimensions != list(
            self.feature_routing_result.mr_dimensions
        ):
            raise ValueError(
                "MR dimensions must match FeatureRoutingResult"
            )
        if (
            self.review_unit_build_result is not None
            and self.review_unit_build_result.schema_version
            in {"review-unit-build-v2", "review-unit-build-v3"}
        ):
            build_source_ids = [
                result.source_ref_id
                for result in self.review_unit_build_result.file_results
            ]
            if set(parse_source_ids) != set(build_source_ids):
                raise ValueError(
                    "AnalysisResult FileParseResults must cover every file result source"
                )
        if self.change_set is not None:
            from arkts_code_reviewer.code_analysis.change_set import ChangeSet

            if not isinstance(self.change_set, ChangeSet):
                raise ValueError("AnalysisResult.change_set must use ChangeSet or None")
            self.change_set.validate()
            if (
                self.review_unit_build_result is None
                or self.review_unit_build_result.schema_version
                != "review-unit-build-v3"
                or self.review_unit_build_result.change_set_id
                != self.change_set.change_set_id
            ):
                raise ValueError(
                    "AnalysisResult ChangeSet must match a review-unit-build-v3 result"
                )
            _validate_change_set_build_graph(
                self.change_set,
                self.review_unit_build_result,
            )
        elif (
            self.review_unit_build_result is not None
            and self.review_unit_build_result.schema_version == "review-unit-build-v3"
        ):
            raise ValueError("review-unit-build-v3 AnalysisResult requires ChangeSet")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "retrieval_query": asdict(self.retrieval_query),
            "review_units": [asdict(unit) for unit in self.review_units],
            "metadata": asdict(self.metadata),
            "review_unit_build_result": (
                None
                if self.review_unit_build_result is None
                else asdict(self.review_unit_build_result)
            ),
            "file_parse_results": [
                {"analysis": result.analysis.to_dict()}
                for result in self.file_parse_results
            ],
            "unit_fact_scopes": [scope.to_dict() for scope in self.unit_fact_scopes],
            "feature_routing_result": self.feature_routing_result.to_dict(),
        }
        if self.change_set is not None:
            payload["change_set"] = self.change_set.to_dict()
        return payload


@dataclass(frozen=True)
class FileHunk:
    new_start: int
    new_lines: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.new_start, int)
            or isinstance(self.new_start, bool)
            or self.new_start < 1
        ):
            raise ValueError("FileHunk.new_start must be an integer >= 1")
        if (
            not isinstance(self.new_lines, int)
            or isinstance(self.new_lines, bool)
            or self.new_lines < 1
        ):
            raise ValueError("FileHunk.new_lines must be an integer >= 1")

    @property
    def new_end(self) -> int:
        return self.new_start + max(0, self.new_lines) - 1


@dataclass(frozen=True)
class FileInput:
    path: str
    content: str
    hunks: list[FileHunk] = field(default_factory=list)
    source_ref: CodeSourceRef | None = None


class CodeParser(Protocol):
    def parse(self, source: str, path: str) -> CodeFacts:
        """Parse source code into deterministic facts."""
