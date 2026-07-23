from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from arkts_code_reviewer.knowledge.document_first._canonical import canonical_hash
from arkts_code_reviewer.knowledge.document_first.projection import (
    DocumentProjectionMappingDraft,
    ProjectionBindingDraft,
    build_document_projection_mapping,
)
from arkts_code_reviewer.knowledge.document_first.semantic_facets import (
    SEMANTIC_FACET_QUALIFICATION,
    SEMANTIC_FACET_SET_SCHEMA_VERSION,
    SEMANTIC_FACET_USE_SCOPE,
    SEMANTIC_RELATION_GRAPH_SCHEMA_VERSION,
    SemanticContextSignatureDraft,
    SemanticFacetDraft,
    SemanticFacetRelationDraft,
    SemanticFacetSetDraft,
    SemanticRelationGraphDraft,
    build_projection_mapping_from_semantic_facet_set,
    build_semantic_facet_set,
    build_semantic_facet_set_from_projection_mapping,
    build_semantic_relation_graph,
    load_semantic_facet_set,
    load_semantic_facet_set_draft,
    load_semantic_relation_graph,
    load_semantic_relation_graph_draft,
    verify_semantic_facet_set,
    verify_semantic_relation_graph,
)
from arkts_code_reviewer.knowledge.document_first.source_atoms import (
    build_source_atom_set,
)
from arkts_code_reviewer.knowledge.document_first.source_fragments import (
    build_source_fragment_set,
    slice_source_fragment_text,
)
from arkts_code_reviewer.knowledge.document_first.structure import (
    build_markdown_document_map,
)
from arkts_code_reviewer.knowledge.models import NormalizedDocument, SourceRef


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _inputs():
    body = (
        "# TaskPool线程\n\n"
        "TaskPool普通任务不能超过3分钟。LongTask不受该时间限制。"
        "TaskPool工作线程不能访问AppStorage。\n\n"
        "# Worker线程\n\n"
        "Worker适合独立运行长时间任务。\n"
    )
    document = NormalizedDocument(
        document_id="openharmony-docs:zh-cn/thread-rules.md",
        source_ref=SourceRef(
            source_id="openharmony-docs",
            revision="b" * 40,
            relative_path="zh-cn/thread-rules.md",
            anchor="document",
            authority="official_documentation",
            content_hash=_sha256(body),
        ),
        media_type="text/markdown",
        title="线程规则",
        heading_tree=(),
        body=body,
        language="zh-CN",
        adapter_version="test-adapter-v1",
    )
    document_map = build_markdown_document_map(document)
    atom_set = build_source_atom_set(document, document_map)
    fragment_set = build_source_fragment_set(document, document_map, atom_set)
    atoms = {atom.atom_id: atom for atom in atom_set.atoms}
    fragment_text = {
        fragment.fragment_id: slice_source_fragment_text(
            document,
            atoms[fragment.atom_id],
            fragment,
        )
        for fragment in fragment_set.fragments
    }
    by_prefix = {
        text.strip().split("。", maxsplit=1)[0]: fragment_id
        for fragment_id, text in fragment_text.items()
    }
    return document, document_map, atom_set, fragment_set, by_prefix


def _draft(document_id: str, fragments: dict[str, str]) -> SemanticFacetSetDraft:
    limit = fragments["TaskPool普通任务不能超过3分钟"]
    long_task = fragments["LongTask不受该时间限制"]
    storage = fragments["TaskPool工作线程不能访问AppStorage"]
    worker = fragments["Worker适合独立运行长时间任务"]
    return SemanticFacetSetDraft(
        document_id=document_id,
        facets=(
            SemanticFacetDraft(
                display_title="TaskPool普通任务执行时间限制",
                category_kinds=("numeric_limit", "constraint"),
                retrieval_aliases=("TaskPool三分钟限制", "任务执行超时"),
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=(limit,),
                    subject_terms=("TaskPool", "线程"),
                    component_terms=("TaskPool",),
                    role_terms=("工作线程池",),
                    scenario_terms=("普通任务执行",),
                    operation_terms=("执行任务",),
                    condition_terms=("非LongTask",),
                ),
            ),
            SemanticFacetDraft(
                display_title="LongTask时间限制例外",
                category_kinds=("exception",),
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=(long_task,),
                    required_context_fragment_ids=(limit,),
                    subject_terms=("LongTask", "线程"),
                    component_terms=("TaskPool",),
                    role_terms=("工作线程池",),
                    scenario_terms=("长任务执行",),
                    condition_terms=("LongTask",),
                ),
            ),
            SemanticFacetDraft(
                display_title="TaskPool线程禁止访问AppStorage",
                category_kinds=(
                    "prohibition",
                    "component_behavior",
                    "lifecycle_and_resource",
                ),
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=(storage,),
                    subject_terms=("TaskPool", "AppStorage", "线程"),
                    component_terms=("AppStorage", "TaskPool"),
                    role_terms=("工作线程池",),
                    scenario_terms=("并发任务执行",),
                    operation_terms=("访问AppStorage",),
                ),
            ),
            SemanticFacetDraft(
                display_title="TaskPool工作线程角色",
                category_kinds=("component_behavior",),
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=(storage,),
                    subject_terms=("TaskPool", "线程"),
                    component_terms=("TaskPool",),
                    role_terms=("工作线程池",),
                ),
            ),
            SemanticFacetDraft(
                display_title="Worker适合长时间任务",
                category_kinds=("applicability", "alternative_and_recommendation"),
                context=SemanticContextSignatureDraft(
                    primary_fragment_ids=(worker,),
                    subject_terms=("Worker", "线程"),
                    component_terms=("Worker",),
                    role_terms=("独立工作线程",),
                    scenario_terms=("长时间任务",),
                ),
            ),
        ),
    )


def _built():
    document, document_map, atom_set, fragment_set, fragments = _inputs()
    facet_set = build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        _draft(document.document_id, fragments),
    )
    return document, document_map, atom_set, fragment_set, fragments, facet_set


def _facet_by_title(facet_set, title: str):
    return next(facet for facet in facet_set.facets if facet.display_title == title)


def test_semantic_facets_support_multi_label_multi_facet_and_context() -> None:
    document, document_map, atom_set, fragment_set, fragments, facet_set = _built()

    assert facet_set.schema_version == SEMANTIC_FACET_SET_SCHEMA_VERSION
    assert facet_set.use_scope == SEMANTIC_FACET_USE_SCOPE
    assert facet_set.qualification == SEMANTIC_FACET_QUALIFICATION
    assert facet_set.evidence_eligible is False
    assert facet_set.production_qualified is False
    assert facet_set.unclassified_fragment_ids == ()

    storage_fragment = fragments["TaskPool工作线程不能访问AppStorage"]
    storage_facets = [
        facet
        for facet in facet_set.facets
        if storage_fragment in facet.context.primary_fragment_ids
    ]
    assert len(storage_facets) == 2
    detailed = _facet_by_title(facet_set, "TaskPool线程禁止访问AppStorage")
    assert detailed.category_kinds == (
        "component_behavior",
        "lifecycle_and_resource",
        "prohibition",
    )
    assert detailed.context.component_terms == ("AppStorage", "TaskPool")

    exception = _facet_by_title(facet_set, "LongTask时间限制例外")
    assert exception.context.required_context_fragment_ids == (
        fragments["TaskPool普通任务不能超过3分钟"],
    )
    verify_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
    )


def test_semantic_facet_set_is_deterministic_and_strictly_loadable() -> None:
    document, document_map, atom_set, fragment_set, fragments, first = _built()
    second = build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        _draft(document.document_id, fragments),
    )

    trusted_inputs = {
        "document": document,
        "document_map": document_map,
        "atom_set": atom_set,
        "fragment_set": fragment_set,
    }
    assert first == second
    assert load_semantic_facet_set(first.model_dump_json(), **trusted_inputs) == first

    changed = first.model_dump(mode="json")
    changed["facet_set_id"] = "semantic-facet-set:sha256:" + "0" * 64
    with pytest.raises(ValueError, match="facet_set_id does not match"):
        load_semantic_facet_set(json.dumps(changed, ensure_ascii=False), **trusted_inputs)

    unknown = first.model_dump(mode="json")
    unknown["model_reasoning"] = "not allowed"
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_semantic_facet_set(json.dumps(unknown, ensure_ascii=False), **trusted_inputs)

    rehashed_incomplete = first.model_dump(mode="json")
    rehashed_incomplete["facets"] = [
        facet
        for facet in rehashed_incomplete["facets"]
        if facet["display_title"] != "Worker适合长时间任务"
    ]
    set_payload = {
        key: value
        for key, value in rehashed_incomplete.items()
        if key != "facet_set_id"
    }
    rehashed_incomplete["facet_set_id"] = canonical_hash(
        "semantic-facet-set",
        set_payload,
    )
    with pytest.raises(ValueError, match="cover every Source Fragment"):
        load_semantic_facet_set(
            json.dumps(rehashed_incomplete, ensure_ascii=False),
            **trusted_inputs,
        )


def test_semantic_facets_bridge_to_existing_projection_v1_without_losing_atoms() -> None:
    document, document_map, atom_set, fragment_set, _, facet_set = _built()

    mapping = build_projection_mapping_from_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
    )
    classified_atoms = {
        atom_id for binding in mapping.bindings for atom_id in binding.atom_ids
    }
    assert classified_atoms | set(mapping.unclassified_atom_ids) == {
        atom.atom_id for atom in atom_set.atoms
    }
    assert len(mapping.bindings) == sum(
        len(facet.category_kinds) for facet in facet_set.facets
    )

    compatibility_facets = build_semantic_facet_set_from_projection_mapping(
        document,
        document_map,
        atom_set,
        fragment_set,
        mapping,
    )
    assert compatibility_facets.fragment_set_id == fragment_set.fragment_set_id
    classified_fragments = {
        fragment_id
        for facet in compatibility_facets.facets
        for fragment_id in facet.context.primary_fragment_ids
    }
    assert classified_fragments | set(compatibility_facets.unclassified_fragment_ids) == {
        fragment.fragment_id for fragment in fragment_set.fragments
    }
    roundtrip_mapping = build_projection_mapping_from_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        compatibility_facets,
    )
    assert roundtrip_mapping == mapping


def test_projection_bridge_rejects_mixed_classification_within_one_atom() -> None:
    document, document_map, atom_set, fragment_set, fragments = _inputs()
    valid = _draft(document.document_id, fragments)
    storage = fragments["TaskPool工作线程不能访问AppStorage"]
    mixed_draft = valid.model_copy(
        update={
            "facets": tuple(
                facet
                for facet in valid.facets
                if storage not in facet.context.primary_fragment_ids
            ),
            "unclassified_fragment_ids": (storage,),
        }
    )
    facet_set = build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        mixed_draft,
    )

    with pytest.raises(ValueError, match="mixes classified and explicitly unclassified"):
        build_projection_mapping_from_semantic_facet_set(
            document,
            document_map,
            atom_set,
            fragment_set,
            facet_set,
        )


def test_projection_bridge_rejects_distinct_facets_that_collapse_to_one_binding() -> None:
    document, document_map, atom_set, fragment_set, fragments = _inputs()
    valid = _draft(document.document_id, fragments)
    original = valid.facets[0]
    different_context = original.context.model_copy(
        update={"scenario_terms": ("另一个语境",)}
    )
    duplicate_after_collapse = original.model_copy(update={"context": different_context})
    facet_set = build_semantic_facet_set(
        document,
        document_map,
        atom_set,
        fragment_set,
        valid.model_copy(update={"facets": (*valid.facets, duplicate_after_collapse)}),
    )

    with pytest.raises(ValueError, match="distinct Facets become the same legacy Binding"):
        build_projection_mapping_from_semantic_facet_set(
            document,
            document_map,
            atom_set,
            fragment_set,
            facet_set,
        )


def test_legacy_projection_bridge_rejects_empty_subject_instead_of_inventing_one() -> None:
    document, document_map, atom_set, fragment_set, _ = _inputs()
    first_atom = atom_set.atoms[0]
    mapping = build_document_projection_mapping(
        document,
        document_map,
        atom_set,
        DocumentProjectionMappingDraft(
            document_id=document.document_id,
            bindings=(
                ProjectionBindingDraft(
                    category_kind="overview",
                    display_title="线程概述",
                    subject_terms=(),
                    retrieval_aliases=(),
                    atom_ids=(first_atom.atom_id,),
                    required_context_atom_ids=(),
                ),
            ),
            unclassified_atom_ids=tuple(
                atom.atom_id for atom in atom_set.atoms if atom != first_atom
            ),
        ),
    )

    with pytest.raises(ValueError, match="without inventing source metadata"):
        build_semantic_facet_set_from_projection_mapping(
            document,
            document_map,
            atom_set,
            fragment_set,
            mapping,
        )


def test_draft_json_loaders_require_explicit_empty_fields() -> None:
    document, _, _, _, fragments = _inputs()
    facet_payload = _draft(document.document_id, fragments).model_dump(mode="json")
    del facet_payload["unclassified_fragment_ids"]
    with pytest.raises(ValueError, match="explicitly provide every contract field"):
        load_semantic_facet_set_draft(json.dumps(facet_payload, ensure_ascii=False))

    nested_payload = _draft(document.document_id, fragments).model_dump(mode="json")
    del nested_payload["facets"][0]["context"]["version_terms"]
    with pytest.raises(ValueError, match="explicitly provide every contract field"):
        load_semantic_facet_set_draft(json.dumps(nested_payload, ensure_ascii=False))

    relation_payload = SemanticRelationGraphDraft(
        document_id=document.document_id,
        relations=(),
    ).model_dump(mode="json")
    del relation_payload["relations"]
    with pytest.raises(ValueError, match="explicitly provide every contract field"):
        load_semantic_relation_graph_draft(json.dumps(relation_payload, ensure_ascii=False))


def test_semantic_facet_builder_rejects_missing_unknown_and_overlapping_coverage() -> None:
    document, document_map, atom_set, fragment_set, fragments = _inputs()
    valid = _draft(document.document_id, fragments)

    missing = valid.model_copy(update={"facets": valid.facets[:-1]})
    with pytest.raises(ValueError, match="cover every Source Fragment"):
        build_semantic_facet_set(document, document_map, atom_set, fragment_set, missing)

    unknown_id = "source-fragment:sha256:" + "f" * 64
    unknown_context = valid.facets[0].context.model_copy(
        update={"required_context_fragment_ids": (unknown_id,)}
    )
    unknown_facet = valid.facets[0].model_copy(update={"context": unknown_context})
    unknown = valid.model_copy(update={"facets": (unknown_facet, *valid.facets[1:])})
    with pytest.raises(ValueError, match="unknown Source Fragment"):
        build_semantic_facet_set(document, document_map, atom_set, fragment_set, unknown)

    overlap = valid.model_copy(
        update={
            "unclassified_fragment_ids": (
                fragments["TaskPool普通任务不能超过3分钟"],
            )
        }
    )
    with pytest.raises(ValueError, match="classified and unclassified"):
        build_semantic_facet_set(document, document_map, atom_set, fragment_set, overlap)


def test_semantic_context_rejects_primary_context_overlap() -> None:
    fragment_id = "source-fragment:sha256:" + "a" * 64
    with pytest.raises(ValidationError, match="must be disjoint"):
        SemanticContextSignatureDraft(
            primary_fragment_ids=(fragment_id,),
            required_context_fragment_ids=(fragment_id,),
            subject_terms=("TaskPool",),
        )


def test_relation_graph_keeps_directed_and_canonical_symmetric_relations() -> None:
    document, document_map, atom_set, fragment_set, _, facet_set = _built()
    limit = _facet_by_title(facet_set, "TaskPool普通任务执行时间限制")
    exception = _facet_by_title(facet_set, "LongTask时间限制例外")
    worker = _facet_by_title(facet_set, "Worker适合长时间任务")
    graph = build_semantic_relation_graph(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
        SemanticRelationGraphDraft(
            document_id=document.document_id,
            relations=(
                SemanticFacetRelationDraft(
                    relation_kind="exception_of",
                    source_facet_id=exception.facet_id,
                    target_facet_id=limit.facet_id,
                ),
                SemanticFacetRelationDraft(
                    relation_kind="same_subject_different_context",
                    source_facet_id=worker.facet_id,
                    target_facet_id=limit.facet_id,
                ),
            ),
        ),
    )

    assert graph.schema_version == SEMANTIC_RELATION_GRAPH_SCHEMA_VERSION
    exception_edge = next(
        relation for relation in graph.relations if relation.relation_kind == "exception_of"
    )
    assert exception_edge.source_facet_id == exception.facet_id
    assert exception_edge.target_facet_id == limit.facet_id
    symmetric = next(
        relation
        for relation in graph.relations
        if relation.relation_kind == "same_subject_different_context"
    )
    assert symmetric.source_facet_id < symmetric.target_facet_id
    assert (
        load_semantic_relation_graph(
            graph.model_dump_json(),
            document=document,
            document_map=document_map,
            atom_set=atom_set,
            fragment_set=fragment_set,
            facet_set=facet_set,
        )
        == graph
    )

    rehashed_unknown = graph.model_dump(mode="json")
    relation = rehashed_unknown["relations"][0]
    relation["target_facet_id"] = "semantic-facet:sha256:" + "f" * 64
    relation_payload = {
        key: value for key, value in relation.items() if key != "relation_id"
    }
    relation["relation_id"] = canonical_hash(
        "semantic-facet-relation",
        relation_payload,
    )
    rehashed_unknown["relations"].sort(key=lambda item: item["relation_id"])
    graph_payload = {
        key: value for key, value in rehashed_unknown.items() if key != "relation_graph_id"
    }
    rehashed_unknown["relation_graph_id"] = canonical_hash(
        "semantic-relation-graph",
        graph_payload,
    )
    with pytest.raises(ValueError, match="unknown Facet"):
        load_semantic_relation_graph(
            json.dumps(rehashed_unknown, ensure_ascii=False),
            document=document,
            document_map=document_map,
            atom_set=atom_set,
            fragment_set=fragment_set,
            facet_set=facet_set,
        )
    verify_semantic_relation_graph(
        document,
        document_map,
        atom_set,
        fragment_set,
        facet_set,
        graph,
    )


def test_relation_graph_rejects_unknown_self_and_duplicate_symmetric_edges() -> None:
    document, document_map, atom_set, fragment_set, _, facet_set = _built()
    first, second = facet_set.facets[:2]

    with pytest.raises(ValidationError, match="same Facet twice"):
        SemanticFacetRelationDraft(
            relation_kind="supplements",
            source_facet_id=first.facet_id,
            target_facet_id=first.facet_id,
        )

    unknown = "semantic-facet:sha256:" + "f" * 64
    with pytest.raises(ValueError, match="unknown Facet"):
        build_semantic_relation_graph(
            document,
            document_map,
            atom_set,
            fragment_set,
            facet_set,
            SemanticRelationGraphDraft(
                document_id=document.document_id,
                relations=(
                    SemanticFacetRelationDraft(
                        relation_kind="supplements",
                        source_facet_id=first.facet_id,
                        target_facet_id=unknown,
                    ),
                ),
            ),
        )

    with pytest.raises(ValueError, match="duplicate canonical relations"):
        build_semantic_relation_graph(
            document,
            document_map,
            atom_set,
            fragment_set,
            facet_set,
            SemanticRelationGraphDraft(
                document_id=document.document_id,
                relations=(
                    SemanticFacetRelationDraft(
                        relation_kind="contrasts_with",
                        source_facet_id=first.facet_id,
                        target_facet_id=second.facet_id,
                    ),
                    SemanticFacetRelationDraft(
                        relation_kind="contrasts_with",
                        source_facet_id=second.facet_id,
                        target_facet_id=first.facet_id,
                    ),
                ),
            ),
        )
