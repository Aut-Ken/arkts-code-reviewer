from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

FindingConfidence = Literal["high", "medium", "low"]
FindingAction = Literal["human_confirm", "ignore_low_confidence", "add_golden_case", "improve_prompt"]
JudgeVerdict = Literal[
    "pass",
    "needs_human_review",
    "likely_parser_bug",
    "invalid_input",
    "invalid_output",
    "dry_run",
]


@dataclass(frozen=True)
class SourceExcerpt:
    text: str
    line_start: int
    line_end: int
    total_lines: int
    truncated: bool


@dataclass(frozen=True)
class ParserSnapshot:
    parser_name: str
    parser_layer: str
    facts: dict[str, Any]
    review_units: list[dict[str, Any]]
    retrieval_units: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationRequest:
    task: str
    prompt_version: str
    sample: dict[str, Any]
    parser_output: ParserSnapshot
    judge_focus: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JudgeFinding:
    kind: str
    field: str
    value: str
    evidence_lines: list[int]
    confidence: FindingConfidence
    reason: str
    suggested_action: FindingAction
    retrieval_impact: Literal["high", "medium", "low", "none"] = "none"
    impact_reason: str = ""


@dataclass(frozen=True)
class JudgeResult:
    sample_id: str
    source_path: str
    llm: dict[str, str]
    verdict: JudgeVerdict
    independent_facts: dict[str, Any]
    findings: list[JudgeFinding]
    review_unit_boundary: dict[str, Any]
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
