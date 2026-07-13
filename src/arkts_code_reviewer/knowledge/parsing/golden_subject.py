from __future__ import annotations

from markdown_it import MarkdownIt

from arkts_code_reviewer.knowledge.models import (
    HeadingNode,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.parsing.clauses import parse_markdown_clauses
from arkts_code_reviewer.knowledge_validation.golden import KnowledgeGoldenCase


def _heading_tree(body: str) -> tuple[HeadingNode, ...]:
    tokens = MarkdownIt("commonmark").enable("table").parse(body)
    headings: list[HeadingNode] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        inline = tokens[index + 1]
        headings.append(
            HeadingNode(
                level=int(token.tag[1:]),
                title=inline.content.strip(),
                span=SourceSpan(start_line=token.map[0] + 1, end_line=token.map[1]),
            )
        )
    return tuple(headings)


def _normalized_document(case: KnowledgeGoldenCase) -> NormalizedDocument:
    source = case.source
    is_markdown = source.relative_path.endswith(".md")
    headings = _heading_tree(source.content) if is_markdown else ()
    title = headings[0].title if headings else source.relative_path.rsplit("/", 1)[-1]
    return NormalizedDocument(
        document_id=f"{source.source_id}:{source.relative_path}",
        source_ref=SourceRef(
            source_id=source.source_id,
            revision=source.revision,
            relative_path=source.relative_path,
            anchor="document",
            authority=source.authority,
            content_hash=source.content_sha256,
        ),
        media_type="text/markdown" if is_markdown else "text/typescript-declaration",
        title=title,
        heading_tree=headings,
        body=source.content,
        language="zh-CN" if is_markdown else "en",
        adapter_version="knowledge-golden-adapter-v1",
    )


def _clause_projection(case: KnowledgeGoldenCase) -> list[dict[str, object]]:
    result = parse_markdown_clauses(
        _normalized_document(case),
        rule_namespace=case.source.rule_namespace,
    )
    clauses: list[dict[str, object]] = []
    for item in result.clauses:
        candidate = item.candidate
        applicability = candidate.applicability
        clauses.append(
            {
                "rule_id": item.rule_id,
                "native_rule_id": candidate.native_rule_id,
                "rule_type": candidate.rule_type,
                "status": item.proposed_status,
                "text": candidate.text,
                "heading_path": list(candidate.heading_path),
                "parent_context": candidate.parent_context,
                "source_span": candidate.source_span.model_dump(mode="json"),
                "applicability": {
                    "min_api_level": applicability.min_api_level,
                    "max_api_level": applicability.max_api_level,
                    "releases": list(applicability.releases),
                    "language_modes": list(applicability.language_modes),
                },
                "examples": [
                    example.model_dump(mode="json") for example in candidate.examples
                ],
            }
        )
    return clauses


def _api_projection(case: KnowledgeGoldenCase) -> list[dict[str, object]]:
    from arkts_code_reviewer.knowledge.parsing.api import parse_api_symbols

    result = parse_api_symbols(_normalized_document(case))
    return [
        {
            "canonical_name": symbol.canonical_name,
            "kind": symbol.kind,
            "signature": symbol.signature,
            "since": symbol.since,
            "deprecated_since": symbol.deprecated_since,
            "source_span": symbol.source_span.model_dump(mode="json"),
        }
        for symbol in result.symbols
    ]


def current_knowledge_subject(case: KnowledgeGoldenCase) -> dict[str, object]:
    if case.source.relative_path.endswith(".md"):
        clauses = _clause_projection(case)
        api_symbols: list[dict[str, object]] = []
    else:
        clauses = []
        api_symbols = _api_projection(case)
    return {
        "clauses": clauses,
        "api_symbols": api_symbols,
        "annotations": [],
    }


__all__ = ["current_knowledge_subject"]
