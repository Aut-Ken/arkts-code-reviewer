from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

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
