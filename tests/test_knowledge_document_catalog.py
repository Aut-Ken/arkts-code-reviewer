from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.document_first.catalog import (
    DocumentCatalogEntry,
    build_document_catalog,
    load_document_catalog,
    verify_document_catalog,
)
from arkts_code_reviewer.knowledge.document_first.models import (
    DocumentCard,
    DocumentCardDraft,
    DocumentSectionSummary,
    MarkdownDocumentMap,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_document_card,
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef

CatalogInput = tuple[NormalizedDocument, MarkdownDocumentMap, DocumentCard]


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _catalog_input(relative_path: str, title: str, api: str) -> CatalogInput:
    body = f"# {title}\n\n## 使用说明\n\n通过 `{api}` 完成示例操作。\n"
    document = NormalizedDocument(
        document_id=f"openharmony-docs:{relative_path}",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path=relative_path,
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256_bytes(body.encode("utf-8")),
        ),
        media_type="text/markdown",
        title=title,
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-adapter-v1",
    )
    document_map = build_markdown_document_map(document)
    draft = DocumentCardDraft(
        document_id=document.document_id,
        summary=f"{title}的导航摘要。",
        primary_topics=(title,),
        important_apis=(api,),
        section_summaries=tuple(
            DocumentSectionSummary(
                section_id=section.section_id,
                summary=f"导航到{section.title}。",
            )
            for section in document_map.sections
        ),
    )
    card = build_document_card(document, document_map, draft)
    return document, document_map, card


def test_catalog_is_content_addressed_sorted_and_order_independent() -> None:
    later = _catalog_input("zh-cn/zeta.md", "Worker", "Worker")
    earlier = _catalog_input("zh-cn/alpha.md", "TaskPool", "taskpool.execute")

    first = build_document_catalog((later, earlier))
    second = build_document_catalog((earlier, later))

    assert first == second
    assert first.catalog_id.startswith("document-catalog:sha256:")
    assert first.document_count == 2
    assert tuple(entry.ordinal for entry in first.entries) == (0, 1)
    assert tuple(entry.document_map.source_ref.relative_path for entry in first.entries) == (
        "zh-cn/alpha.md",
        "zh-cn/zeta.md",
    )
    assert first.use_scope == "navigation_only_not_evidence"
    assert first.evidence_eligible is False
    assert first.production_qualified is False
    assert first.qualification == "navigation_catalog_contract_not_quality_qualified"
    assert canonical_json(first.model_dump(mode="json")) == canonical_json(
        second.model_dump(mode="json")
    )
    verify_document_catalog((later, earlier), first)


def test_catalog_entry_keeps_map_and_card_without_flattening_navigation_fields() -> None:
    document, document_map, card = _catalog_input(
        "zh-cn/taskpool.md", "TaskPool", "taskpool.execute"
    )

    catalog = build_document_catalog(((document, document_map, card),))
    entry = catalog.entries[0]

    assert entry.document_map == document_map
    assert entry.document_card == card
    assert set(entry.model_dump(mode="json")) == {"ordinal", "document_map", "document_card"}
    assert entry.document_card.use_scope == "navigation_only_not_evidence"
    assert entry.document_card.evidence_eligible is False


def test_catalog_rejects_duplicate_documents_and_empty_input() -> None:
    item = _catalog_input("zh-cn/taskpool.md", "TaskPool", "taskpool.execute")

    with pytest.raises(ValueError, match="at least one trusted document"):
        build_document_catalog(())
    with pytest.raises(ValueError, match="duplicate document_id"):
        build_document_catalog((item, item))


def test_catalog_rejects_map_card_mismatch() -> None:
    first = _catalog_input("zh-cn/taskpool.md", "TaskPool", "taskpool.execute")
    second = _catalog_input("zh-cn/worker.md", "Worker", "Worker")
    stale_identity_card = first[2].model_copy(update={"summary": "被篡改的摘要"})

    with pytest.raises(ValidationError, match="card does not match its Markdown document map"):
        DocumentCatalogEntry(
            ordinal=0,
            document_map=first[1],
            document_card=second[2],
        )
    with pytest.raises(ValidationError, match="card_id does not match"):
        DocumentCatalogEntry(
            ordinal=0,
            document_map=first[1],
            document_card=stale_identity_card,
        )
    with pytest.raises(
        ValueError,
        match="Document card does not match the trusted Markdown document map",
    ):
        build_document_catalog(((first[0], first[1], second[2]),))


def test_catalog_rebuilds_map_and_card_from_each_trusted_document() -> None:
    item = _catalog_input("zh-cn/taskpool.md", "TaskPool", "taskpool.execute")
    catalog = build_document_catalog((item,))
    changed_document = item[0].model_copy(
        update={"body": "# TaskPool\n\n内容已经改变。\n"}
    )

    with pytest.raises(ValueError, match="does not match the trusted normalized document"):
        build_document_catalog(((changed_document, item[1], item[2]),))
    with pytest.raises(ValueError, match="does not match the trusted normalized document"):
        verify_document_catalog(((changed_document, item[1], item[2]),), catalog)


def test_strict_catalog_loader_round_trips_and_rejects_tampering() -> None:
    first = _catalog_input("zh-cn/taskpool.md", "TaskPool", "taskpool.execute")
    second = _catalog_input("zh-cn/worker.md", "Worker", "Worker")
    catalog = build_document_catalog((first, second))
    raw = catalog.model_dump_json()

    assert load_document_catalog(raw) == catalog

    changed_summary = catalog.model_dump(mode="json")
    changed_summary["entries"][0]["document_card"]["summary"] = "被篡改的摘要"
    with pytest.raises(ValueError, match="card_id does not match"):
        load_document_catalog(json.dumps(changed_summary, ensure_ascii=False))

    changed_map = catalog.model_dump(mode="json")
    changed_map["entries"][0]["document_map"]["title"] = "被篡改的标题"
    with pytest.raises(ValueError, match="map_id does not match"):
        load_document_catalog(json.dumps(changed_map, ensure_ascii=False))

    changed_id = catalog.model_dump(mode="json")
    changed_id["catalog_id"] = "document-catalog:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="catalog_id does not match"):
        load_document_catalog(json.dumps(changed_id, ensure_ascii=False))

    elevated = catalog.model_dump(mode="json")
    elevated["production_qualified"] = True
    with pytest.raises(ValueError, match="production_qualified"):
        load_document_catalog(json.dumps(elevated, ensure_ascii=False))

    unknown = catalog.model_dump(mode="json")
    unknown["evidence"] = "forbidden"
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_document_catalog(json.dumps(unknown, ensure_ascii=False))

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_document_catalog('{"schema_version":"document-catalog-build-v1","x":1,"x":2}')


def test_catalog_loader_rejects_reordered_and_cross_bound_entries() -> None:
    first = _catalog_input("zh-cn/taskpool.md", "TaskPool", "taskpool.execute")
    second = _catalog_input("zh-cn/worker.md", "Worker", "Worker")
    catalog = build_document_catalog((first, second))

    reordered = catalog.model_dump(mode="json")
    reordered["entries"] = list(reversed(reordered["entries"]))
    with pytest.raises(ValueError, match="ordinals must be contiguous and ordered"):
        load_document_catalog(json.dumps(reordered, ensure_ascii=False))

    cross_bound = catalog.model_dump(mode="json")
    cross_bound["entries"][0]["document_card"] = cross_bound["entries"][1]["document_card"]
    with pytest.raises(ValueError, match="card does not match its Markdown document map"):
        load_document_catalog(json.dumps(cross_bound, ensure_ascii=False))
