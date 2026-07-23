from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.document_first.models import (
    DOCUMENT_STRUCTURE_FRONT_MATTER_BUILDER_VERSION,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    SOURCE_ATOM_BUILDER_VERSION,
    SOURCE_ATOM_SET_SCHEMA_VERSION,
    SourceAtomSet,
    build_source_atom_set,
    load_source_atom_set,
    slice_source_atom_text,
    verify_source_atom_set,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _document(body: str, *, path: str = "zh-cn/taskpool.md") -> NormalizedDocument:
    return NormalizedDocument(
        document_id=f"openharmony-docs:{path}",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path=path,
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256_bytes(body.encode("utf-8")),
        ),
        media_type="text/markdown",
        title="TaskPool",
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-adapter-v1",
    )


def _covered_lines(atom_set: SourceAtomSet) -> tuple[int, ...]:
    spans = tuple(atom.source_span for atom in atom_set.atoms) + tuple(
        region.source_span for region in atom_set.regions
    )
    return tuple(
        line
        for span in sorted(spans, key=lambda item: item.start_line)
        for line in range(span.start_line, span.end_line + 1)
    )


_STRUCTURED_MARKDOWN = """
# TaskPool

Intro line one.
Intro line two.

- first item
  - nested item
- second item

| API | Limit |
| --- | --- |
| execute | 3 min |

```ts
taskpool.execute(work)
```

> [!NOTE]
> Keep this context.

<!-- internal comment -->

[worker-ref]: worker.md
"""


def test_build_source_atom_set_partitions_every_line_and_keeps_only_hashes() -> None:
    document = _document(_STRUCTURED_MARKDOWN)
    document_map = build_markdown_document_map(document)

    atom_set = build_source_atom_set(document, document_map)

    assert atom_set.schema_version == SOURCE_ATOM_SET_SCHEMA_VERSION
    assert atom_set.builder_version == SOURCE_ATOM_BUILDER_VERSION
    assert atom_set.atom_set_id.startswith("source-atom-set:sha256:")
    assert atom_set.document_id == document.document_id
    assert atom_set.source_ref == document.source_ref
    assert atom_set.document_map_id == document_map.map_id
    assert atom_set.normalized_body_hash == document_map.normalized_body_hash
    assert atom_set.source_line_count == document_map.source_line_count
    assert atom_set.use_scope == "retrieval_projection_input_only_not_evidence"
    assert atom_set.evidence_eligible is False
    assert atom_set.production_qualified is False
    assert atom_set.qualification == "structural_atom_contract_not_quality_qualified"
    assert tuple(atom.ordinal for atom in atom_set.atoms) == tuple(range(len(atom_set.atoms)))
    assert tuple(region.ordinal for region in atom_set.regions) == tuple(
        range(len(atom_set.regions))
    )
    assert _covered_lines(atom_set) == tuple(range(1, document_map.source_line_count + 1))

    assert tuple(atom.kind for atom in atom_set.atoms) == (
        "paragraph",
        "list_item",
        "list_item",
        "table",
        "code_block",
        "note",
        "raw_block",
    )
    assert "heading" in {region.kind for region in atom_set.regions}
    assert "blank" in {region.kind for region in atom_set.regions}
    assert "html_comment" in {region.kind for region in atom_set.regions}
    assert all(atom.required_context_atom_ids == () for atom in atom_set.atoms)
    section_ids = {item.section_id for item in document_map.sections}
    assert all(atom.section_id in section_ids for atom in atom_set.atoms)
    assert all("text" not in atom.model_dump(mode="json") for atom in atom_set.atoms)
    assert all("body" not in atom.model_dump(mode="json") for atom in atom_set.atoms)

    nested_list = atom_set.atoms[1]
    assert slice_source_atom_text(document, nested_list) == "- first item\n  - nested item\n"
    assert slice_source_atom_text(document, atom_set.atoms[-1]) == ("[worker-ref]: worker.md\n")
    verify_source_atom_set(document, document_map, atom_set)


def test_source_atom_set_is_deterministic_content_addressed_and_strictly_loadable() -> None:
    document = _document(_STRUCTURED_MARKDOWN)
    document_map = build_markdown_document_map(document)

    first = build_source_atom_set(document, document_map)
    second = build_source_atom_set(document, document_map)
    raw = first.model_dump_json()

    assert first == second
    assert canonical_json(first.model_dump(mode="json")) == canonical_json(
        second.model_dump(mode="json")
    )
    assert load_source_atom_set(raw) == first
    assert load_source_atom_set(raw.encode("utf-8")) == first

    changed_identity = first.model_dump(mode="json")
    changed_identity["atom_set_id"] = "source-atom-set:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="atom_set_id does not match"):
        load_source_atom_set(json.dumps(changed_identity, ensure_ascii=False))

    unknown = first.model_dump(mode="json")
    unknown["source_body"] = _STRUCTURED_MARKDOWN
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_source_atom_set(json.dumps(unknown, ensure_ascii=False))

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_source_atom_set('{"schema_version":"source-atom-set-v1","x":1,"x":2}')
    with pytest.raises(ValueError, match="top-level value must be an object"):
        load_source_atom_set("[]")
    with pytest.raises(ValueError, match="must use UTF-8"):
        load_source_atom_set(b"\xff")


def test_source_atom_set_models_reject_broken_coverage_and_context_references() -> None:
    document = _document(_STRUCTURED_MARKDOWN)
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)

    missing_region = atom_set.model_dump(mode="json")
    missing_region["regions"] = missing_region["regions"][1:]
    with pytest.raises(ValidationError, match="region ordinals|cover every physical source line"):
        SourceAtomSet.model_validate(missing_region)

    unknown_context = atom_set.model_dump(mode="json")
    unknown_context["atoms"][0]["required_context_atom_ids"] = ["source-atom:sha256:" + "f" * 64]
    with pytest.raises(ValidationError, match="unknown Atom"):
        SourceAtomSet.model_validate(unknown_context)

    boolean_ordinal = atom_set.model_dump(mode="json")
    boolean_ordinal["atoms"][0]["ordinal"] = False
    with pytest.raises(ValidationError, match="valid integer"):
        SourceAtomSet.model_validate(boolean_ordinal)


def test_verifier_and_slice_reject_a_different_trusted_document() -> None:
    document = _document(_STRUCTURED_MARKDOWN)
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)
    changed_document = document.model_copy(update={"body": document.body.replace("3 min", "5 min")})

    with pytest.raises(ValueError, match="does not match the trusted normalized document"):
        verify_source_atom_set(changed_document, document_map, atom_set)
    table_atom = next(atom for atom in atom_set.atoms if atom.kind == "table")
    with pytest.raises(ValueError, match="text hash does not match"):
        slice_source_atom_text(changed_document, table_atom)


def test_heading_only_document_is_rejected_before_projection_compilation() -> None:
    document = _document("# Empty section\n\n", path="zh-cn/empty.md")
    document_map = build_markdown_document_map(document)

    with pytest.raises(ValueError, match="at least one eligible Markdown content block"):
        build_source_atom_set(document, document_map)


def test_yaml_front_matter_is_preserved_as_non_content_region() -> None:
    document = _document(
        "---\n"
        "title: TaskPool\n"
        "aliases:\n"
        "  - Worker\n"
        "---\n"
        "\n"
        "# TaskPool\n"
        "\n"
        "Use TaskPool for concurrent work.\n",
        path="zh-cn/front-matter.md",
    )
    document_map = build_markdown_document_map(document)

    atom_set = build_source_atom_set(document, document_map)

    assert document_map.builder_version == DOCUMENT_STRUCTURE_FRONT_MATTER_BUILDER_VERSION
    assert [section.title for section in document_map.sections] == [
        "Document preamble",
        "TaskPool",
    ]
    front_matter = next(region for region in atom_set.regions if region.kind == "front_matter")
    assert (front_matter.source_span.start_line, front_matter.source_span.end_line) == (1, 5)
    assert tuple(atom.kind for atom in atom_set.atoms) == ("paragraph",)
    assert slice_source_atom_text(document, atom_set.atoms[0]) == (
        "Use TaskPool for concurrent work.\n"
    )
    assert _covered_lines(atom_set) == tuple(range(1, 10))
    verify_source_atom_set(document, document_map, atom_set)


def test_repeated_heading_titles_are_valid_ancestry_not_duplicate_metadata() -> None:
    document = _document(
        "# Same\n\n## Same\n\nRepeated heading content.\n",
        path="zh-cn/repeated-heading.md",
    )
    document_map = build_markdown_document_map(document)

    atom_set = build_source_atom_set(document, document_map)

    assert len(atom_set.atoms) == 1
    assert atom_set.atoms[0].heading_path == ("Same", "Same")
    assert slice_source_atom_text(document, atom_set.atoms[0]) == ("Repeated heading content.\n")
    verify_source_atom_set(document, document_map, atom_set)
