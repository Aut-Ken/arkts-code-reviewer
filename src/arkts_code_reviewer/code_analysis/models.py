from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

from arkts_code_reviewer.code_analysis.review_unit_contract import (
    REVIEW_UNIT_DIAGNOSTIC_CODES,
    REVIEW_UNIT_KINDS,
    SELECTION_REASONS,
    ReviewUnitDiagnosticCode,
    ReviewUnitKind,
    SelectionReason,
    declaration_unit_id,
    fallback_unit_id,
)

ParserLayer = Literal["L0", "L1", "parse_degraded"]
AnalysisMode = Literal["full", "diff"]


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
    kind: Literal[
        "struct", "class", "function", "method", "build_method", "builder", "ui_block"
    ]
    name: str
    qualified_name: str
    span: SourceSpan
    parent_name: str | None = None
    text: str = ""

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
        data = asdict(self)
        for key in (
            "components",
            "apis",
            "decorators",
            "attributes",
            "symbols",
            "syntax",
        ):
            data[key] = sorted(getattr(self, key))
        return data


@dataclass(frozen=True)
class CodeFeatures:
    components: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

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
        if self.code not in REVIEW_UNIT_DIAGNOSTIC_CODES:
            raise ValueError(f"unsupported ReviewUnit diagnostic code: {self.code}")
        if any(
            not isinstance(line, int) or isinstance(line, bool) or line < 1
            for line in self.lines
        ):
            raise ValueError("ReviewUnitDiagnostic.lines must be 1-based")
        if list(self.lines) != sorted(set(self.lines)):
            raise ValueError("ReviewUnitDiagnostic.lines must be sorted and unique")


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
        if self.unit_kind not in REVIEW_UNIT_KINDS:
            raise ValueError(f"unsupported ReviewUnit kind: {self.unit_kind}")
        if self.selection_reason not in SELECTION_REASONS:
            raise ValueError(
                f"unsupported ReviewUnit selection reason: {self.selection_reason}"
            )
        if not isinstance(self.context_degraded, bool):
            raise ValueError("ReviewUnit.context_degraded must be a boolean")
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
            )
            if self.unit_kind == "fallback"
            else declaration_unit_id(
                self.file,
                self.unit_kind,
                self.unit_symbol,
                self.source_span.start_line,
                self.source_span.end_line,
            )
        )
        if self.unit_id != expected_id:
            raise ValueError("ReviewUnit.unit_id does not match its identity fields")

        for values, name in (
            (self.changed_lines, "changed_lines"),
            (self.file_changed_lines, "file_changed_lines"),
            (self.unit_changed_lines, "unit_changed_lines"),
            (self.changed_new_lines, "changed_new_lines"),
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

        expected_changed_new_lines = [
            line
            for line in self.file_changed_lines
            if self.context_span.contains_line(line)
        ]
        if self.changed_new_lines != expected_changed_new_lines:
            raise ValueError(
                "ReviewUnit.changed_new_lines must project file_changed_lines into context"
            )
        expected_unit_changed_lines = [
            line - self.context_span.start_line + 1
            for line in self.changed_new_lines
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


@dataclass(frozen=True)
class RetrievalUnit:
    unit_ref: str
    code_features: CodeFeatures
    intent_summary: str


@dataclass(frozen=True)
class MrContext:
    triggered_dimensions: list[str]
    token_budget: int


@dataclass(frozen=True)
class RetrievalQuery:
    mr_context: MrContext
    units: list[RetrievalUnit]


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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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


class CodeParser(Protocol):
    def parse(self, source: str, path: str) -> CodeFacts:
        """Parse source code into deterministic facts."""
