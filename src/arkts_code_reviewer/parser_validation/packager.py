from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.models import CodeFacts, ReviewUnit
from arkts_code_reviewer.code_analysis.parser_factory import (
    ParserChoice,
    create_code_parser,
    parser_display_name,
)
from arkts_code_reviewer.parser_validation.manifest import SampleEntry
from arkts_code_reviewer.parser_validation.models import (
    ParserSnapshot,
    SourceExcerpt,
    ValidationRequest,
)

PROMPT_VERSION = "parser-judge-v1"
JUDGE_FOCUS = [
    "components",
    "apis",
    "decorators",
    "attributes",
    "declaration_boundaries",
    "review_unit_boundaries",
    "tags",
]


def build_validation_request(
    *,
    engine_root: Path,
    sample: SampleEntry,
    max_source_lines: int = 240,
    parser_name: ParserChoice = "arkts-tree-sitter",
) -> ValidationRequest:
    source_path = engine_root / Path(sample.path)
    source = source_path.read_text(encoding="utf-8")
    parser = create_code_parser(parser_name)
    facts = parser.parse(source, sample.path)
    analyzer = CodeAnalyzer(parser=parser)
    analysis = analyzer.analyze_file(path=sample.path, content=source, mode="full")
    excerpt = numbered_excerpt(source, max_lines=max_source_lines)

    return ValidationRequest(
        task="arkts_parser_validation",
        prompt_version=PROMPT_VERSION,
        sample={
            "id": sample.sample_id,
            "path": sample.path,
            "category": sample.category,
            "source_excerpt": excerpt.text,
            "excerpt_line_start": excerpt.line_start,
            "excerpt_line_end": excerpt.line_end,
            "source_total_lines": excerpt.total_lines,
            "source_truncated": excerpt.truncated,
        },
        parser_output=ParserSnapshot(
            parser_name=parser_display_name(parser_name),
            parser_layer=facts.parser_layer,
            facts=_compact_facts(facts),
            review_units=[_compact_review_unit(item) for item in analysis.review_units],
            retrieval_units=[asdict(item) for item in analysis.retrieval_query.units],
            warnings=facts.warnings + analysis.metadata.warnings,
        ),
        judge_focus=JUDGE_FOCUS,
    )


def numbered_excerpt(source: str, *, max_lines: int = 240, start_line: int = 1) -> SourceExcerpt:
    lines = source.splitlines()
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    start_index = start_line - 1
    selected = lines[start_index : start_index + max_lines]
    line_end = start_index + len(selected)
    text = "\n".join(
        f"{line_no:04d}: {line}" for line_no, line in enumerate(selected, start=start_line)
    )
    return SourceExcerpt(
        text=text,
        line_start=start_line,
        line_end=line_end,
        total_lines=len(lines),
        truncated=line_end < len(lines),
    )


def request_to_json_dict(request: ValidationRequest) -> dict[str, Any]:
    return request.to_dict()


def _compact_facts(facts: CodeFacts) -> dict[str, Any]:
    return {
        "path": facts.path,
        "imports": [asdict(item) for item in facts.imports],
        "components": sorted(facts.components),
        "apis": sorted(facts.apis),
        "decorators": sorted(facts.decorators),
        "attributes": sorted(facts.attributes),
        "symbols": sorted(facts.symbols),
        "syntax": sorted(facts.syntax),
        "declarations": [
            {
                "kind": item.kind,
                "name": item.name,
                "qualified_name": item.qualified_name,
                "parent_name": item.parent_name,
                "span": asdict(item.span),
                "line_count": item.line_count,
            }
            for item in facts.declarations
        ],
        "parser_layer": facts.parser_layer,
        "warnings": facts.warnings,
    }


def _compact_review_unit(unit: ReviewUnit) -> dict[str, Any]:
    return {
        "file": unit.file,
        "unit_symbol": unit.unit_symbol,
        "unit_ref": unit.unit_ref,
        "full_text_line_count": len(unit.full_text.splitlines()),
        "full_text_chars": len(unit.full_text),
        "changed_lines": unit.changed_lines,
        "file_changed_lines": unit.file_changed_lines,
        "unit_changed_lines": unit.unit_changed_lines,
        "host_summary": asdict(unit.host_summary),
        "context_degraded": unit.context_degraded,
    }
