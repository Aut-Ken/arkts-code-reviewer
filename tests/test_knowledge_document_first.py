from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first import (
    DocumentCardDraft,
    DocumentSectionSummary,
    MarkdownDocumentMap,
    build_document_card,
    build_markdown_document_map,
    load_document_card,
    load_document_card_draft,
    load_markdown_document_map,
    verify_document_card,
    verify_markdown_document_map,
)
from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/golden/knowledge/sources"


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _document(
    body: str,
    *,
    title: str = "测试文档",
    raw_source: bytes | None = None,
    release: str | None = None,
    api_level: int | None = None,
    language_mode: str | None = None,
) -> NormalizedDocument:
    raw = raw_source if raw_source is not None else body.encode("utf-8")
    return NormalizedDocument(
        document_id="openharmony-docs:zh-cn/test.md",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path="zh-cn/test.md",
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256_bytes(raw),
        ),
        media_type="text/markdown",
        title=title,
        heading_tree=(),
        body=body,
        language="zh-CN",
        release=release,
        api_level=api_level,
        language_mode=language_mode,
        adapter_version="test-adapter-v1",
    )


def _draft_for(document_map: MarkdownDocumentMap) -> DocumentCardDraft:
    sections = document_map.sections
    return DocumentCardDraft(
        document_id=document_map.document_id,
        summary="说明状态管理及父子组件同步的适用场景。",
        primary_topics=("状态管理", "ArkUI"),
        important_apis=("@Link",),
        section_summaries=tuple(
            DocumentSectionSummary(
                section_id=section.section_id,
                summary=f"导航摘要：{section.title}",
            )
            for section in sections
        ),
    )


def test_nested_markdown_builds_atomic_and_subtree_ranges() -> None:
    body = (FIXTURES / "KG008_parent_context.md").read_text(encoding="utf-8")
    document = _document(body, title="状态管理")

    document_map = build_markdown_document_map(document)

    assert [section.title for section in document_map.sections] == [
        "状态管理",
        "父子组件同步",
        "@Link",
    ]
    assert [
        (section.content_span.start_line, section.content_span.end_line)
        for section in document_map.sections
    ] == [(1, 2), (3, 4), (5, 7)]
    assert [
        (section.subtree_span.start_line, section.subtree_span.end_line)
        for section in document_map.sections
    ] == [(1, 7), (3, 7), (5, 7)]
    assert [section.heading_path for section in document_map.sections] == [
        ("状态管理",),
        ("状态管理", "父子组件同步"),
        ("状态管理", "父子组件同步", "@Link"),
    ]
    assert document_map.sections[1].parent_section_id == document_map.sections[0].section_id
    assert document_map.sections[2].parent_section_id == document_map.sections[1].section_id

    lines = body.splitlines(keepends=True)
    assert document_map.sections[1].subtree_text_hash == _sha256_bytes(
        "".join(lines[2:7]).encode("utf-8")
    )
    verify_markdown_document_map(document, document_map)


def test_map_is_deterministic_and_repeated_titles_do_not_collide() -> None:
    document = _document("# Root\n\n## Same\n\nA\n\n## Same\n\nB\n", title="Root")

    first = build_markdown_document_map(document)
    second = build_markdown_document_map(document)

    assert first == second
    assert canonical_json(first.model_dump(mode="json")) == canonical_json(
        second.model_dump(mode="json")
    )
    same_ids = [section.section_id for section in first.sections if section.title == "Same"]
    assert len(same_ids) == 2
    assert len(set(same_ids)) == 2


def test_markdown_parser_supports_setext_but_ignores_fake_headings() -> None:
    document = _document(
        "Root\n====\n\n"
        "```ts\n# Fake in fence\n```\n\n"
        "> ## Quoted heading\n\n"
        "Real child\n----------\n",
        title="Root",
    )

    document_map = build_markdown_document_map(document)

    assert [section.title for section in document_map.sections] == ["Root", "Real child"]
    assert [section.heading_level for section in document_map.sections] == [1, 2]
    assert document_map.sections[1].parent_section_id == document_map.sections[0].section_id


def test_markdown_line_mapping_uses_lf_not_unicode_splitlines() -> None:
    body = "# Root\nParagraph\u2028continued\n\n## Child\nBody\n"
    document_map = build_markdown_document_map(_document(body, title="Root"))

    assert document_map.source_line_count == 5
    child = document_map.sections[1]
    assert (child.content_span.start_line, child.content_span.end_line) == (4, 5)
    assert child.content_text_hash == _sha256_bytes(b"## Child\nBody\n")


def test_headingless_document_is_preserved_instead_of_disappearing() -> None:
    document = _document("TaskPool 是一个多线程任务执行环境。\n")

    document_map = build_markdown_document_map(document)

    assert len(document_map.sections) == 1
    assert document_map.sections[0].kind == "document_body"
    assert document_map.sections[0].content_span.start_line == 1
    assert document_map.sections[0].content_span.end_line == 1
    assert document_map.diagnostics == ("missing_navigation_heading",)


def test_map_records_raw_source_and_normalized_body_hashes_separately() -> None:
    raw = b"\xef\xbb\xbf# Title\r\n\r\nBody\r\n"
    normalized = "# Title\n\nBody\n"
    document = _document(normalized, title="Title", raw_source=raw)

    document_map = build_markdown_document_map(document)

    assert document_map.source_ref.content_hash == _sha256_bytes(raw)
    assert document_map.normalized_body_hash == _sha256_bytes(normalized.encode("utf-8"))
    assert document_map.source_ref.content_hash != document_map.normalized_body_hash


def test_map_preserves_authoritative_version_and_adapter_metadata() -> None:
    document = _document(
        "# Title\n\nBody\n",
        title="Title",
        release="HarmonyOS NEXT",
        api_level=18,
        language_mode="static",
    )

    document_map = build_markdown_document_map(document)

    assert document_map.release == "HarmonyOS NEXT"
    assert document_map.api_level == 18
    assert document_map.language_mode == "static"
    assert document_map.adapter_version == "test-adapter-v1"
    assert document_map.normalization_diagnostics == ()


def test_card_projects_ai_navigation_without_granting_evidence_authority() -> None:
    body = (FIXTURES / "KG008_parent_context.md").read_text(encoding="utf-8")
    document = _document(body, title="状态管理")
    document_map = build_markdown_document_map(document)
    draft = _draft_for(document_map)

    card = build_document_card(document, document_map, draft)

    assert card.document_map_id == document_map.map_id
    assert card.source_ref == document_map.source_ref
    assert card.normalized_body_hash == document_map.normalized_body_hash
    assert card.primary_topics == ("ArkUI", "状态管理")
    assert card.use_scope == "navigation_only_not_evidence"
    assert card.evidence_eligible is False
    verify_document_card(document, document_map, card)


def test_card_requires_exact_section_coverage_in_map_order() -> None:
    document = _document("# Root\n\n## Child\n\nBody\n", title="Root")
    document_map = build_markdown_document_map(document)
    draft = _draft_for(document_map)
    reversed_draft = draft.model_copy(
        update={"section_summaries": tuple(reversed(draft.section_summaries))}
    )

    with pytest.raises(ValueError, match="every mapped section exactly once"):
        build_document_card(document, document_map, reversed_draft)

    with pytest.raises(ValidationError, match="unique IDs"):
        DocumentCardDraft(
            document_id=draft.document_id,
            summary=draft.summary,
            primary_topics=draft.primary_topics,
            important_apis=draft.important_apis,
            section_summaries=(draft.section_summaries[0], draft.section_summaries[0]),
        )


def test_strict_json_rejects_duplicate_unknown_and_tampered_content() -> None:
    document = _document("# Root\n\nBody\n", title="Root")
    document_map = build_markdown_document_map(document)
    card = build_document_card(document, document_map, _draft_for(document_map))

    draft_payload = _draft_for(document_map).model_dump(mode="json")
    draft_payload["exact_quote"] = "模型不得产生证据字段"
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_document_card_draft(json.dumps(draft_payload, ensure_ascii=False))

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_document_card_draft('{"schema_version":"document-card-draft-v1","x":1,"x":2}')

    card_payload = card.model_dump(mode="json")
    card_payload["summary"] = "被篡改的摘要"
    with pytest.raises(ValueError, match="card_id does not match"):
        load_document_card(json.dumps(card_payload, ensure_ascii=False))

    map_payload = document_map.model_dump(mode="json")
    map_payload["title"] = "被篡改的标题"
    with pytest.raises(ValueError, match="map_id does not match"):
        load_markdown_document_map(json.dumps(map_payload, ensure_ascii=False))


def test_strict_map_json_rejects_nested_value_coercion() -> None:
    document_map = build_markdown_document_map(_document("# Root\n\nBody\n", title="Root"))

    string_line_payload = document_map.model_dump(mode="json")
    string_line_payload["sections"][0]["content_span"]["start_line"] = "1"
    with pytest.raises(ValueError, match="start_line must be an integer"):
        load_markdown_document_map(json.dumps(string_line_payload, ensure_ascii=False))

    unprefixed_hash_payload = document_map.model_dump(mode="json")
    content_hash = unprefixed_hash_payload["source_ref"]["content_hash"]
    unprefixed_hash_payload["source_ref"]["content_hash"] = content_hash.removeprefix("sha256:")
    with pytest.raises(ValueError, match="canonical sha256"):
        load_markdown_document_map(json.dumps(unprefixed_hash_payload, ensure_ascii=False))


def test_old_map_and_card_fail_against_changed_normalized_source() -> None:
    document = _document("# Root\n\nOld body\n", title="Root")
    document_map = build_markdown_document_map(document)
    card = build_document_card(document, document_map, _draft_for(document_map))
    changed_document = document.model_copy(update={"body": "# Root\n\nChanged body\n"})
    changed_map = build_markdown_document_map(changed_document)

    with pytest.raises(ValueError, match="does not match the trusted normalized document"):
        verify_markdown_document_map(changed_document, document_map)
    with pytest.raises(ValueError, match="does not match the trusted Markdown document map"):
        verify_document_card(changed_document, changed_map, card)


def test_empty_or_non_markdown_documents_fail_closed() -> None:
    with pytest.raises(ValueError, match="non-empty Markdown"):
        build_markdown_document_map(_document(" \n"))

    declaration = _document("declare class Example {}\n").model_copy(
        update={"media_type": "text/typescript-declaration"}
    )
    with pytest.raises(ValueError, match="requires text/markdown"):
        build_markdown_document_map(declaration)
