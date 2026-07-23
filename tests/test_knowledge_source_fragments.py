from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_hash
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    build_source_atom_set,
    slice_source_atom_text,
)
from arkts_code_reviewer.knowledge.document_first.source_fragments import (
    SOURCE_FRAGMENT_BUILDER_VERSION,
    SOURCE_FRAGMENT_MAX_CODEPOINTS,
    SOURCE_FRAGMENT_OFFSET_UNIT,
    SOURCE_FRAGMENT_SET_SCHEMA_VERSION,
    SourceFragment,
    SourceFragmentSet,
    build_source_fragment_set,
    load_source_fragment_set,
    slice_source_fragment_text,
    verify_source_fragment_set,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _document(body: str) -> NormalizedDocument:
    return NormalizedDocument(
        document_id="openharmony-docs:zh-cn/thread-context.md",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path="zh-cn/thread-context.md",
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256(body),
        ),
        media_type="text/markdown",
        title="线程上下文",
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-adapter-v1",
    )


_BODY = (
    "# 线程上下文\n"
    "\n"
    "TaskPool 😀普通任务不能超过3分钟。LongTask不受该时间限制。"
    "TaskPool工作线程不能访问AppStorage。\n"
    "\n"
    "| 类型 | 用途 |\n"
    "| --- | --- |\n"
    "| TaskPool | 短任务 |\n"
    "\n"
    "```ts\n"
    "taskpool.execute(work)\n"
    "```\n"
)


def _inputs(body: str = _BODY):
    document = _document(body)
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)
    fragment_set = build_source_fragment_set(document, document_map, atom_set)
    return document, document_map, atom_set, fragment_set


def test_source_fragments_partition_every_atom_and_keep_utf8_exact() -> None:
    document, document_map, atom_set, fragment_set = _inputs()

    assert fragment_set.schema_version == SOURCE_FRAGMENT_SET_SCHEMA_VERSION
    assert fragment_set.builder_version == SOURCE_FRAGMENT_BUILDER_VERSION
    assert fragment_set.offset_unit == SOURCE_FRAGMENT_OFFSET_UNIT
    assert fragment_set.atom_count == len(atom_set.atoms)
    assert fragment_set.fragment_count == len(fragment_set.fragments)
    assert fragment_set.evidence_eligible is False
    assert fragment_set.production_qualified is False

    fragments_by_atom: dict[str, list[SourceFragment]] = {}
    for fragment in fragment_set.fragments:
        fragments_by_atom.setdefault(fragment.atom_id, []).append(fragment)
    for atom in atom_set.atoms:
        fragment_text = "".join(
            slice_source_fragment_text(document, atom, fragment)
            for fragment in fragments_by_atom[atom.atom_id]
        )
        assert fragment_text == slice_source_atom_text(document, atom)

    paragraph = next(atom for atom in atom_set.atoms if atom.kind == "paragraph")
    paragraph_fragments = fragments_by_atom[paragraph.atom_id]
    assert len(paragraph_fragments) == 3
    assert tuple(fragment.kind for fragment in paragraph_fragments) == (
        "sentence",
        "sentence",
        "sentence",
    )
    assert "😀" in slice_source_fragment_text(document, paragraph, paragraph_fragments[0])

    for atom in atom_set.atoms:
        if atom.kind in {"table", "code_block"}:
            fragments = fragments_by_atom[atom.atom_id]
            assert len(fragments) == 1
            assert fragments[0].kind == "whole_atom"

    verify_source_fragment_set(document, document_map, atom_set, fragment_set)


def test_source_fragment_builder_bounds_a_large_prose_atom_without_rewriting() -> None:
    long_text = "并发上下文" + "甲" * (SOURCE_FRAGMENT_MAX_CODEPOINTS + 80)
    body = f"# 大段落\n\n{long_text}\n"
    document, _, atom_set, fragment_set = _inputs(body)
    paragraph = next(atom for atom in atom_set.atoms if atom.kind == "paragraph")
    fragments = tuple(
        fragment for fragment in fragment_set.fragments if fragment.atom_id == paragraph.atom_id
    )

    assert len(fragments) == 2
    assert fragments[0].kind == "bounded_segment"
    assert "".join(
        slice_source_fragment_text(document, paragraph, fragment) for fragment in fragments
    ) == slice_source_atom_text(document, paragraph)


def test_source_fragment_builder_marks_an_unterminated_tail_as_bounded_segment() -> None:
    body = "# 尾段\n\n第一句。最后一段没有句号"
    document, _, atom_set, fragment_set = _inputs(body)
    paragraph = next(atom for atom in atom_set.atoms if atom.kind == "paragraph")
    fragments = tuple(
        fragment for fragment in fragment_set.fragments if fragment.atom_id == paragraph.atom_id
    )

    assert tuple(fragment.kind for fragment in fragments) == (
        "sentence",
        "bounded_segment",
    )
    assert "".join(
        slice_source_fragment_text(document, paragraph, fragment) for fragment in fragments
    ) == slice_source_atom_text(document, paragraph)


def test_source_fragment_set_is_deterministic_content_addressed_and_strict() -> None:
    document, document_map, atom_set, first = _inputs()
    second = build_source_fragment_set(document, document_map, atom_set)
    trusted_inputs = {
        "document": document,
        "document_map": document_map,
        "atom_set": atom_set,
    }

    assert first == second
    assert load_source_fragment_set(first.model_dump_json(), **trusted_inputs) == first
    assert (
        load_source_fragment_set(
            first.model_dump_json().encode("utf-8"),
            **trusted_inputs,
        )
        == first
    )

    changed_id = first.model_dump(mode="json")
    changed_id["fragment_set_id"] = "source-fragment-set:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="fragment_set_id does not match"):
        load_source_fragment_set(
            json.dumps(changed_id, ensure_ascii=False),
            **trusted_inputs,
        )

    unknown = first.model_dump(mode="json")
    unknown["source_body"] = _BODY
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_source_fragment_set(json.dumps(unknown, ensure_ascii=False), **trusted_inputs)

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_source_fragment_set(
            '{"schema_version":"source-fragment-set-v1","x":1,"x":2}',
            **trusted_inputs,
        )

    rehashed = first.model_dump(mode="json")
    rehashed_fragment = rehashed["fragments"][0]
    rehashed_fragment["kind"] = "bounded_segment"
    fragment_payload = {
        key: value for key, value in rehashed_fragment.items() if key != "fragment_id"
    }
    rehashed_fragment["fragment_id"] = canonical_hash("source-fragment", fragment_payload)
    set_payload = {key: value for key, value in rehashed.items() if key != "fragment_set_id"}
    rehashed["fragment_set_id"] = canonical_hash("source-fragment-set", set_payload)
    with pytest.raises(ValueError, match="trusted source inputs"):
        load_source_fragment_set(
            json.dumps(rehashed, ensure_ascii=False),
            **trusted_inputs,
        )


def test_source_fragment_set_rejects_gaps_even_after_ids_are_rehashed() -> None:
    _, _, _, fragment_set = _inputs()
    payload = fragment_set.model_dump(mode="json")
    first = payload["fragments"][0]
    first["relative_utf8_span"]["end_byte"] -= 1
    fragment_payload = {key: value for key, value in first.items() if key != "fragment_id"}
    first["fragment_id"] = canonical_hash("source-fragment", fragment_payload)
    set_payload = {key: value for key, value in payload.items() if key != "fragment_set_id"}
    payload["fragment_set_id"] = canonical_hash("source-fragment-set", set_payload)

    with pytest.raises(ValidationError, match="without gaps or overlaps"):
        SourceFragmentSet.model_validate(payload)


def test_fragment_slice_rejects_a_non_utf8_boundary_and_wrong_atom() -> None:
    document, _, atom_set, fragment_set = _inputs()
    paragraph = next(atom for atom in atom_set.atoms if atom.kind == "paragraph")
    fragment = next(
        item for item in fragment_set.fragments if item.atom_id == paragraph.atom_id
    )
    paragraph_text = slice_source_atom_text(document, paragraph)
    emoji_start = paragraph_text.encode("utf-8").index("😀".encode())
    payload = fragment.model_dump(mode="json", exclude={"fragment_id"})
    payload["relative_utf8_span"]["start_byte"] = emoji_start + 1
    payload["fragment_id"] = canonical_hash("source-fragment", payload)
    broken = SourceFragment.model_validate(payload)

    with pytest.raises(ValueError, match="not valid UTF-8"):
        slice_source_fragment_text(document, paragraph, broken)

    other_atom = next(atom for atom in atom_set.atoms if atom.atom_id != paragraph.atom_id)
    with pytest.raises(ValueError, match="does not belong"):
        slice_source_fragment_text(document, other_atom, fragment)


def test_source_fragment_verifier_rejects_a_different_trusted_document() -> None:
    document, document_map, atom_set, fragment_set = _inputs()
    changed = document.model_copy(update={"body": document.body.replace("3分钟", "5分钟")})

    with pytest.raises(ValueError, match="trusted normalized document"):
        verify_source_fragment_set(changed, document_map, atom_set, fragment_set)
