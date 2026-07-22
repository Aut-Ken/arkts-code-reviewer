from __future__ import annotations

from dataclasses import dataclass

from markdown_it import MarkdownIt

from arkts_code_reviewer.knowledge.document_first._canonical import (
    canonical_hash,
    sha256_text,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DOCUMENT_CARD_USE_SCOPE,
    DOCUMENT_STRUCTURE_BUILDER_VERSION,
    DocumentCard,
    DocumentCardDraft,
    MarkdownDocumentMap,
    MarkdownSection,
    _DocumentCardPayload,
    _MarkdownDocumentMapPayload,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceSpan


@dataclass(frozen=True)
class _ParsedHeading:
    level: int
    title: str
    start_line: int
    end_line: int


def _source_lines(body: str) -> tuple[str, ...]:
    parts = body.split("\n")
    lines = tuple(f"{part}\n" for part in parts[:-1])
    if parts[-1]:
        return (*lines, parts[-1])
    return lines


def _slice_lines(lines: tuple[str, ...], span: SourceSpan) -> str:
    return "".join(lines[span.start_line - 1 : span.end_line])


def _parse_headings(body: str) -> tuple[_ParsedHeading, ...]:
    tokens = MarkdownIt("commonmark").enable("table").parse(body)
    headings: list[_ParsedHeading] = []
    blockquote_depth = 0
    for index, token in enumerate(tokens):
        if token.type == "blockquote_open":
            blockquote_depth += 1
            continue
        if token.type == "blockquote_close":
            blockquote_depth -= 1
            continue
        if token.type != "heading_open" or token.map is None or blockquote_depth:
            continue
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        if inline is None or inline.type != "inline" or not inline.content.strip():
            continue
        headings.append(
            _ParsedHeading(
                level=int(token.tag[1:]),
                title=inline.content.strip(),
                start_line=token.map[0] + 1,
                end_line=token.map[1],
            )
        )
    return tuple(headings)


def _section_id(
    document: NormalizedDocument,
    *,
    ordinal: int,
    kind: str,
    title: str,
    start_line: int,
    normalized_body_hash: str,
) -> str:
    return canonical_hash(
        "document-section",
        {
            "document_id": document.document_id,
            "source_ref": document.source_ref.model_dump(mode="json"),
            "normalized_body_hash": normalized_body_hash,
            "ordinal": ordinal,
            "kind": kind,
            "title": title,
            "start_line": start_line,
        },
    )


def build_markdown_document_map(document: NormalizedDocument) -> MarkdownDocumentMap:
    document = NormalizedDocument.model_validate(document.model_dump(mode="json"))
    if document.media_type != "text/markdown":
        raise ValueError("Document-First structure currently requires text/markdown")
    if not document.body.strip():
        raise ValueError("Document-First structure requires non-empty Markdown content")

    lines = _source_lines(document.body)
    line_count = len(lines)
    normalized_body_hash = sha256_text(document.body)
    headings = _parse_headings(document.body)
    diagnostics: set[str] = set()
    sections: list[MarkdownSection] = []

    if not headings:
        span = SourceSpan(start_line=1, end_line=line_count)
        sections.append(
            MarkdownSection(
                section_id=_section_id(
                    document,
                    ordinal=0,
                    kind="document_body",
                    title=document.title,
                    start_line=1,
                    normalized_body_hash=normalized_body_hash,
                ),
                ordinal=0,
                kind="document_body",
                title=document.title,
                heading_level=None,
                heading_path=(),
                parent_section_id=None,
                heading_span=None,
                content_span=span,
                subtree_span=span,
                content_text_hash=sha256_text(_slice_lines(lines, span)),
                subtree_text_hash=sha256_text(_slice_lines(lines, span)),
            )
        )
        diagnostics.add("missing_navigation_heading")
    else:
        first_heading_start = headings[0].start_line
        preamble_text = "".join(lines[: first_heading_start - 1])
        if preamble_text.strip():
            span = SourceSpan(start_line=1, end_line=first_heading_start - 1)
            sections.append(
                MarkdownSection(
                    section_id=_section_id(
                        document,
                        ordinal=0,
                        kind="preamble",
                        title="Document preamble",
                        start_line=1,
                        normalized_body_hash=normalized_body_hash,
                    ),
                    ordinal=0,
                    kind="preamble",
                    title="Document preamble",
                    heading_level=None,
                    heading_path=(),
                    parent_section_id=None,
                    heading_span=None,
                    content_span=span,
                    subtree_span=span,
                    content_text_hash=sha256_text(_slice_lines(lines, span)),
                    subtree_text_hash=sha256_text(_slice_lines(lines, span)),
                )
            )

        heading_sections: list[MarkdownSection] = []
        stack: list[tuple[_ParsedHeading, str, tuple[str, ...]]] = []
        for heading_index, heading in enumerate(headings):
            while stack and stack[-1][0].level >= heading.level:
                stack.pop()
            if stack and heading.level > stack[-1][0].level + 1:
                diagnostics.add(f"heading_level_jump:line_{heading.start_line}")

            parent_id = stack[-1][1] if stack else None
            parent_path = stack[-1][2] if stack else ()
            heading_path = (*parent_path, heading.title)
            content_end = (
                headings[heading_index + 1].start_line - 1
                if heading_index + 1 < len(headings)
                else line_count
            )
            subtree_end = line_count
            for candidate in headings[heading_index + 1 :]:
                if candidate.level <= heading.level:
                    subtree_end = candidate.start_line - 1
                    break

            ordinal = len(sections) + len(heading_sections)
            section_id = _section_id(
                document,
                ordinal=ordinal,
                kind="heading",
                title=heading.title,
                start_line=heading.start_line,
                normalized_body_hash=normalized_body_hash,
            )
            content_span = SourceSpan(start_line=heading.start_line, end_line=content_end)
            subtree_span = SourceSpan(start_line=heading.start_line, end_line=subtree_end)
            heading_sections.append(
                MarkdownSection(
                    section_id=section_id,
                    ordinal=ordinal,
                    kind="heading",
                    title=heading.title,
                    heading_level=heading.level,
                    heading_path=heading_path,
                    parent_section_id=parent_id,
                    heading_span=SourceSpan(
                        start_line=heading.start_line,
                        end_line=heading.end_line,
                    ),
                    content_span=content_span,
                    subtree_span=subtree_span,
                    content_text_hash=sha256_text(_slice_lines(lines, content_span)),
                    subtree_text_hash=sha256_text(_slice_lines(lines, subtree_span)),
                )
            )
            stack.append((heading, section_id, heading_path))
        sections.extend(heading_sections)

    payload = _MarkdownDocumentMapPayload(
        document_id=document.document_id,
        source_ref=document.source_ref,
        title=document.title,
        language=document.language,
        release=document.release,
        api_level=document.api_level,
        language_mode=document.language_mode,
        adapter_version=document.adapter_version,
        normalization_diagnostics=document.diagnostics,
        normalized_body_hash=normalized_body_hash,
        source_line_count=line_count,
        sections=tuple(sections),
        builder_version=DOCUMENT_STRUCTURE_BUILDER_VERSION,
        diagnostics=tuple(sorted(diagnostics)),
    ).model_dump(mode="json")
    payload["map_id"] = canonical_hash("markdown-document-map", payload)
    return MarkdownDocumentMap.model_validate(payload)


def verify_markdown_document_map(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
) -> None:
    rebuilt = build_markdown_document_map(document)
    if rebuilt != document_map:
        raise ValueError("Markdown document map does not match the trusted normalized document")


def build_document_card(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    draft: DocumentCardDraft,
) -> DocumentCard:
    verify_markdown_document_map(document, document_map)
    document_map = MarkdownDocumentMap.model_validate(document_map.model_dump(mode="json"))
    draft = DocumentCardDraft.model_validate(draft.model_dump(mode="json"))
    if draft.document_id != document_map.document_id:
        raise ValueError("Document card draft document_id does not match the document map")
    expected_section_ids = tuple(section.section_id for section in document_map.sections)
    actual_section_ids = tuple(item.section_id for item in draft.section_summaries)
    if actual_section_ids != expected_section_ids:
        raise ValueError(
            "Document card draft must cover every mapped section exactly once and in map order"
        )

    payload = _DocumentCardPayload(
        document_id=document_map.document_id,
        summary=draft.summary,
        primary_topics=tuple(sorted(draft.primary_topics)),
        important_apis=tuple(sorted(draft.important_apis)),
        section_summaries=draft.section_summaries,
        document_map_id=document_map.map_id,
        source_ref=document_map.source_ref,
        normalized_body_hash=document_map.normalized_body_hash,
        use_scope=DOCUMENT_CARD_USE_SCOPE,
        evidence_eligible=False,
    ).model_dump(mode="json")
    payload["card_id"] = canonical_hash("document-card", payload)
    return DocumentCard.model_validate(payload)


def verify_document_card(
    document: NormalizedDocument,
    document_map: MarkdownDocumentMap,
    card: DocumentCard,
) -> None:
    try:
        draft = DocumentCardDraft(
            document_id=card.document_id,
            summary=card.summary,
            primary_topics=card.primary_topics,
            important_apis=card.important_apis,
            section_summaries=card.section_summaries,
        )
        rebuilt = build_document_card(document, document_map, draft)
    except (TypeError, ValueError) as exc:
        raise ValueError("Document card does not match the trusted Markdown document map") from exc
    if rebuilt != card:
        raise ValueError("Document card does not match the trusted Markdown document map")


__all__ = [
    "build_document_card",
    "build_markdown_document_map",
    "verify_document_card",
    "verify_markdown_document_map",
]
