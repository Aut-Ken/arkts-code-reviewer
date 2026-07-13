from __future__ import annotations

from functools import lru_cache

from markdown_it import MarkdownIt

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation import (
    annotate_api_symbol,
    annotate_clause,
)
from arkts_code_reviewer.knowledge.annotation_config import (
    KnowledgeAnnotationConfig,
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.models import (
    HeadingNode,
    KnowledgeAnnotation,
    NormalizedDocument,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.parsing.api import (
    ApiCatalogParseResult,
    parse_api_symbols,
)
from arkts_code_reviewer.knowledge.parsing.clauses import (
    ClauseParseResult,
    parse_markdown_clauses,
)
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


@lru_cache(maxsize=1)
def _annotation_config() -> KnowledgeAnnotationConfig:
    return load_knowledge_annotation_config(
        feature_config=load_default_feature_config()
    )


def _clause_projection(result: ClauseParseResult) -> list[dict[str, object]]:
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


def _api_projection(result: ApiCatalogParseResult) -> list[dict[str, object]]:
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


def _annotation_projection(
    annotation: KnowledgeAnnotation,
    target_id: str,
) -> dict[str, object]:
    return {
        "target_id": target_id,
        "tags": list(annotation.tags),
        "dimension_ids": list(annotation.dimension_ids),
        "apis": list(annotation.apis),
        "domains": list(annotation.domains),
    }


def current_knowledge_subject(case: KnowledgeGoldenCase) -> dict[str, object]:
    document = _normalized_document(case)
    feature_config = load_default_feature_config()
    annotation_config = _annotation_config()
    index_version = "knowledge-golden-annotation-v1"
    if case.source.relative_path.endswith(".md"):
        clause_result = parse_markdown_clauses(
            document,
            rule_namespace=case.source.rule_namespace,
        )
        clauses = _clause_projection(clause_result)
        api_symbols: list[dict[str, object]] = []
        annotations = [
            _annotation_projection(
                annotate_clause(
                    clause,
                    catalog={},
                    feature_config=feature_config,
                    config=annotation_config,
                    index_version=index_version,
                ),
                clause.rule_id,
            )
            for clause in clause_result.clauses
        ]
    else:
        clauses = []
        api_result = parse_api_symbols(document)
        api_symbols = _api_projection(api_result)
        catalog = {symbol.canonical_name: symbol for symbol in api_result.symbols}
        annotations = [
            _annotation_projection(
                annotate_api_symbol(
                    symbol,
                    catalog=catalog,
                    feature_config=feature_config,
                    config=annotation_config,
                    index_version=index_version,
                ),
                symbol.canonical_name,
            )
            for symbol in api_result.symbols
        ]
    return {
        "clauses": clauses,
        "api_symbols": api_symbols,
        "annotations": annotations,
    }


__all__ = ["current_knowledge_subject"]
