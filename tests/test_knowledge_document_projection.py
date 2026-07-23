from __future__ import annotations

import hashlib
import json

import pytest

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_hash
from arkts_code_reviewer.knowledge.document_first.models import MarkdownDocumentMap
from arkts_code_reviewer.knowledge.document_first.projection import (
    DocumentProjection,
    DocumentProjectionMapping,
    DocumentProjectionMappingDraft,
    DocumentProjectionRecord,
    DocumentProjectionVerification,
    ProjectionBindingDraft,
    _projection_id,
    build_document_projection_mapping,
    build_document_projection_record,
    compile_document_projection,
    load_document_projection,
    load_document_projection_mapping,
    load_document_projection_mapping_draft,
    load_document_projection_record,
    load_document_projection_verification,
    load_projection_manifest,
    verify_document_projection,
    verify_document_projection_mapping,
    verify_document_projection_record,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    SourceAtomSet,
    build_source_atom_set,
    slice_source_atom_text,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _document() -> NormalizedDocument:
    body = (
        "# TaskPool\n"
        "\n"
        "TaskPool 提供并发任务执行能力。UNIQUE_OVERVIEW\n"
        "\n"
        "## 限制\n"
        "\n"
        "任务的 CPU 执行不能超过三分钟。UNIQUE_LIMIT\n"
        "\n"
        "- 禁止访问 AppStorage。UNIQUE_PROHIBITION\n"
        "- 需要固定线程时使用 Worker。UNIQUE_ALTERNATIVE\n"
        "\n"
        "```ts\n"
        "taskpool.execute(task); // UNIQUE_EXAMPLE\n"
        "```\n"
    )
    return NormalizedDocument(
        document_id="openharmony-docs:zh-cn/taskpool.md",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="a" * 40,
            relative_path="zh-cn/taskpool.md",
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


def _source_inputs() -> tuple[NormalizedDocument, MarkdownDocumentMap, SourceAtomSet]:
    document = _document()
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)
    assert len(atom_set.atoms) >= 5
    return document, document_map, atom_set


def _complete_draft(
    document: NormalizedDocument,
    atom_set: SourceAtomSet,
    *,
    injected_title: str | None = None,
) -> DocumentProjectionMappingDraft:
    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    return DocumentProjectionMappingDraft(
        document_id=document.document_id,
        bindings=(
            ProjectionBindingDraft(
                category_kind="overview",
                display_title=("TaskPool 文档概览" if injected_title is None else injected_title),
                subject_terms=("执行能力", "TaskPool"),
                retrieval_aliases=("任务池", "并发任务"),
                atom_ids=atom_ids,
            ),
            ProjectionBindingDraft(
                category_kind="prohibition",
                display_title="TaskPool 禁止事项",
                subject_terms=("AppStorage",),
                retrieval_aliases=("工作线程状态",),
                atom_ids=(atom_ids[0],),
            ),
        ),
    )


def _record_inputs() -> tuple[
    NormalizedDocument,
    MarkdownDocumentMap,
    SourceAtomSet,
    DocumentProjectionMapping,
    DocumentProjection,
    DocumentProjectionVerification,
    DocumentProjectionRecord,
]:
    document, document_map, atom_set = _source_inputs()
    mapping = build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        _complete_draft(document, atom_set),
    )
    projection = compile_document_projection(document, document_map, atom_set, mapping)
    verification = verify_document_projection(
        document,
        document_map,
        atom_set,
        mapping,
        projection,
    )
    record = build_document_projection_record(
        document,
        document_map,
        atom_set,
        mapping,
        projection,
    )
    return document, document_map, atom_set, mapping, projection, verification, record


def test_mapping_allows_multi_category_and_canonicalizes_input_order() -> None:
    document, document_map, atom_set = _source_inputs()
    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    first = _complete_draft(document, atom_set)
    second = DocumentProjectionMappingDraft(
        document_id=document.document_id,
        bindings=(
            first.bindings[1],
            first.bindings[0].model_copy(
                update={
                    "subject_terms": tuple(reversed(first.bindings[0].subject_terms)),
                    "retrieval_aliases": tuple(reversed(first.bindings[0].retrieval_aliases)),
                    "atom_ids": tuple(reversed(first.bindings[0].atom_ids)),
                }
            ),
        ),
    )

    first_mapping = build_document_projection_mapping(document, document_map, atom_set, first)
    second_mapping = build_document_projection_mapping(document, document_map, atom_set, second)

    assert first_mapping == second_mapping
    assert tuple(binding.binding_id for binding in first_mapping.bindings) == tuple(
        sorted(binding.binding_id for binding in first_mapping.bindings)
    )
    assert sum(atom_ids[0] in binding.atom_ids for binding in first_mapping.bindings) == 2
    assert first_mapping.use_scope == "retrieval_projection_only_not_evidence"
    assert first_mapping.evidence_eligible is False
    verify_document_projection_mapping(document, document_map, atom_set, first_mapping)


def test_mapping_rejects_unknown_missing_overlap_and_duplicate_bindings() -> None:
    document, document_map, atom_set = _source_inputs()
    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    unknown = "source-atom:sha256:" + "f" * 64

    with pytest.raises(ValueError, match="must be non-empty and trimmed"):
        DocumentProjectionMappingDraft(document_id=" padded ", bindings=())

    with pytest.raises(ValueError, match="unknown Source Atom"):
        build_document_projection_mapping(
            document,
            document_map,
            atom_set,
            DocumentProjectionMappingDraft(
                document_id=document.document_id,
                bindings=(
                    ProjectionBindingDraft(
                        category_kind="overview",
                        display_title="未知 Atom",
                        atom_ids=(*atom_ids, unknown),
                    ),
                ),
            ),
        )

    with pytest.raises(ValueError, match="cover every eligible Source Atom exactly"):
        build_document_projection_mapping(
            document,
            document_map,
            atom_set,
            DocumentProjectionMappingDraft(
                document_id=document.document_id,
                bindings=(
                    ProjectionBindingDraft(
                        category_kind="overview",
                        display_title="覆盖不完整",
                        atom_ids=atom_ids[:-1],
                    ),
                ),
            ),
        )

    with pytest.raises(ValueError, match="classified and unclassified"):
        build_document_projection_mapping(
            document,
            document_map,
            atom_set,
            DocumentProjectionMappingDraft(
                document_id=document.document_id,
                bindings=(
                    ProjectionBindingDraft(
                        category_kind="overview",
                        display_title="错误交集",
                        atom_ids=atom_ids,
                    ),
                ),
                unclassified_atom_ids=(atom_ids[0],),
            ),
        )

    repeated = ProjectionBindingDraft(
        category_kind="overview",
        display_title="重复 Binding",
        atom_ids=atom_ids,
    )
    with pytest.raises(ValueError, match="duplicate canonical bindings"):
        build_document_projection_mapping(
            document,
            document_map,
            atom_set,
            DocumentProjectionMappingDraft(
                document_id=document.document_id,
                bindings=(repeated, repeated),
            ),
        )


def test_unclassified_atoms_are_complete_and_remain_in_projection() -> None:
    document, document_map, atom_set = _source_inputs()
    atom_ids = tuple(atom.atom_id for atom in atom_set.atoms)
    draft = DocumentProjectionMappingDraft(
        document_id=document.document_id,
        bindings=(
            ProjectionBindingDraft(
                category_kind="overview",
                display_title="已分类入口",
                atom_ids=(atom_ids[0],),
            ),
        ),
        unclassified_atom_ids=atom_ids[1:],
    )

    mapping = build_document_projection_mapping(document, document_map, atom_set, draft)
    projection = compile_document_projection(document, document_map, atom_set, mapping)

    assert mapping.unclassified_atom_ids == tuple(sorted(atom_ids[1:]))
    assert projection.manifest.unclassified_count == len(atom_ids) - 1
    assert "### 未分类兜底" in projection.markdown
    for atom in atom_set.atoms[1:]:
        assert atom.atom_id.rsplit(":", maxsplit=1)[1] in projection.markdown
        assert slice_source_atom_text(document, atom) in projection.markdown


def test_projection_is_deterministic_and_renders_each_atom_body_once() -> None:
    document, document_map, atom_set = _source_inputs()
    mapping = build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        _complete_draft(document, atom_set),
    )

    first = compile_document_projection(document, document_map, atom_set, mapping)
    second = compile_document_projection(document, document_map, atom_set, mapping)

    assert first == second
    assert first.projection_id == second.projection_id
    assert first.manifest.markdown_sha256 == second.manifest.markdown_sha256
    assert first.manifest.ordered_atom_ids == tuple(atom.atom_id for atom in atom_set.atoms)
    for atom in atom_set.atoms:
        source_text = slice_source_atom_text(document, atom)
        assert first.markdown.count(source_text) == 1
    assert first.markdown.count("UNIQUE_OVERVIEW") == 1
    assert first.markdown.count("UNIQUE_PROHIBITION") == 1
    assert first.markdown.count("UNIQUE_EXAMPLE") == 1


def test_projection_escapes_model_generated_title_instead_of_injecting_markdown() -> None:
    document, document_map, atom_set = _source_inputs()
    dangerous_title = "[危险](https://bad.example)# *粗体* | <script>"
    mapping = build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        _complete_draft(document, atom_set, injected_title=dangerous_title),
    )

    projection = compile_document_projection(document, document_map, atom_set, mapping)

    assert dangerous_title not in projection.markdown
    assert "- \\[危险\\](https://bad.example)\\# \\*粗体\\* \\| &lt;script&gt;" in (
        projection.markdown
    )
    assert "- [危险](https://bad.example)" not in projection.markdown
    assert "<script>" not in projection.markdown


def test_projection_strict_loaders_round_trip_and_reject_tampering() -> None:
    (
        _document_value,
        _document_map,
        _atom_set,
        mapping,
        projection,
        verification,
        record,
    ) = _record_inputs()
    draft = DocumentProjectionMappingDraft(
        document_id=mapping.document_id,
        bindings=tuple(
            ProjectionBindingDraft(
                category_kind=binding.category_kind,
                display_title=binding.display_title,
                subject_terms=binding.subject_terms,
                retrieval_aliases=binding.retrieval_aliases,
                atom_ids=binding.atom_ids,
                required_context_atom_ids=binding.required_context_atom_ids,
            )
            for binding in mapping.bindings
        ),
        unclassified_atom_ids=mapping.unclassified_atom_ids,
    )

    assert load_document_projection_mapping_draft(draft.model_dump_json()) == draft
    assert load_document_projection_mapping(mapping.model_dump_json()) == mapping
    assert load_document_projection(projection.model_dump_json()) == projection
    assert load_projection_manifest(projection.manifest.model_dump_json()) == projection.manifest
    assert load_document_projection_verification(verification.model_dump_json()) == verification
    assert load_document_projection_record(record.model_dump_json()) == record

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_document_projection_mapping_draft(
            '{"schema_version":"document-projection-mapping-draft-v1",'
            '"document_id":"x","bindings":[],"bindings":[]}'
        )

    unknown_field = draft.model_dump(mode="json")
    unknown_field["evidence"] = "forbidden"
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_document_projection_mapping_draft(json.dumps(unknown_field, ensure_ascii=False))

    changed_mapping = mapping.model_dump(mode="json")
    changed_mapping["bindings"][0]["display_title"] = "被篡改的标题"
    with pytest.raises(ValueError, match="binding_id does not match"):
        load_document_projection_mapping(json.dumps(changed_mapping, ensure_ascii=False))

    changed_projection = projection.model_dump(mode="json")
    changed_projection["markdown"] += "\n篡改正文\n"
    with pytest.raises(ValueError, match="manifest does not match"):
        load_document_projection(json.dumps(changed_projection, ensure_ascii=False))

    changed_manifest = projection.manifest.model_dump(mode="json")
    changed_manifest["atom_count"] += 1
    with pytest.raises(ValueError, match="atom_count does not match"):
        load_projection_manifest(json.dumps(changed_manifest, ensure_ascii=False))

    changed_verification = verification.model_dump(mode="json")
    changed_verification["covered_line_count"] -= 1
    with pytest.raises(ValueError, match="physical line coverage must be complete"):
        load_document_projection_verification(json.dumps(changed_verification, ensure_ascii=False))

    changed_record = record.model_dump(mode="json")
    changed_record["document"]["body"] += "篡改"
    with pytest.raises(ValueError, match="record_id does not match"):
        load_document_projection_record(json.dumps(changed_record, ensure_ascii=False))


def test_verification_and_record_rebuild_fail_closed_on_stale_projection() -> None:
    (
        document,
        document_map,
        atom_set,
        mapping,
        projection,
        verification,
        record,
    ) = _record_inputs()

    assert verification.result == "pass"
    assert verification.physical_line_count == verification.covered_line_count
    assert verification.eligible_atom_count == verification.mapped_atom_count
    assert verification.canonical_atom_body_occurrence_min == 1
    assert verification.canonical_atom_body_occurrence_max == 1
    assert verification.source_text_mutation_count == 0
    assert verification.unknown_atom_reference_count == 0
    assert verification.scoring_duplicate_atom_count == 0
    assert verification.qualification == (
        "mechanically_verified_projection_not_semantically_reviewed"
    )
    assert verification.evidence_eligible is False
    assert verification.production_qualified is False
    verify_document_projection_record(record)

    stale_projection = projection.model_copy(
        update={"markdown": projection.markdown + "\n过期投影\n"}
    )
    with pytest.raises(ValueError, match="does not match the trusted source and Mapping"):
        verify_document_projection(
            document,
            document_map,
            atom_set,
            mapping,
            stale_projection,
        )

    stale_record = record.model_copy(update={"projection": stale_projection})
    with pytest.raises(ValueError, match="manifest does not match"):
        verify_document_projection_record(stale_record)


def test_record_loader_rebuilds_instead_of_trusting_caller_rehashed_pass() -> None:
    record = _record_inputs()[-1]
    raw = record.model_dump(mode="json")
    unknown = "source-atom:sha256:" + "f" * 64

    def rehash(node: dict[str, object], key: str, prefix: str) -> None:
        node[key] = canonical_hash(
            prefix,
            {item_key: value for item_key, value in node.items() if item_key != key},
        )

    binding = raw["mapping"]["bindings"][0]
    binding["atom_ids"] = sorted([*binding["atom_ids"][1:], unknown])
    rehash(binding, "binding_id", "projection-binding")
    raw["mapping"]["bindings"].sort(key=lambda item: item["binding_id"])
    rehash(raw["mapping"], "mapping_id", "document-projection-mapping")

    projection = raw["projection"]
    projection["mapping_id"] = raw["mapping"]["mapping_id"]
    projection["projection_id"] = _projection_id(
        document_id=projection["document_id"],
        source_ref=record.document.source_ref,
        document_map_id=projection["document_map_id"],
        atom_set_id=projection["atom_set_id"],
        mapping_id=projection["mapping_id"],
    )
    manifest = projection["manifest"]
    manifest["projection_id"] = projection["projection_id"]
    manifest["mapping_id"] = projection["mapping_id"]
    manifest["ordered_binding_ids"] = sorted(
        item["binding_id"] for item in raw["mapping"]["bindings"]
    )
    rehash(manifest, "manifest_id", "document-projection-manifest")

    verification = raw["verification"]
    verification["projection_id"] = projection["projection_id"]
    verification["manifest_id"] = manifest["manifest_id"]
    verification["mapping_id"] = raw["mapping"]["mapping_id"]
    rehash(verification, "verification_id", "document-projection-verification")
    rehash(raw, "record_id", "document-projection-record")

    forged = DocumentProjectionRecord.model_validate(raw)
    assert forged.verification.result == "pass"
    with pytest.raises(ValueError, match="unknown Source Atom"):
        verify_document_projection_record(forged)
    with pytest.raises(ValueError, match="unknown Source Atom"):
        load_document_projection_record(json.dumps(raw, ensure_ascii=False))
