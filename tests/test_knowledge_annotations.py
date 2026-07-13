from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from arkts_code_reviewer.feature_routing.config import (
    FeatureConfig,
    load_default_feature_config,
)
from arkts_code_reviewer.knowledge.annotation import (
    annotate_api_symbol,
    annotate_clause,
    build_knowledge_annotations,
)
from arkts_code_reviewer.knowledge.annotation_config import (
    KnowledgeAnnotationConfig,
    load_knowledge_annotation_config,
)
from arkts_code_reviewer.knowledge.extraction import KnowledgeExtractionBuild
from arkts_code_reviewer.knowledge.models import (
    ApiSymbol,
    Applicability,
    ClauseCandidate,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.parsing import ExtractedClause

ROOT = Path(__file__).resolve().parents[1]
REAL_EXTRACTION = Path(
    "/home/autken/Code/arkts-review-data/normalized/knowledge-seed-v1/candidates.json"
)


def _source_ref(relative_path: str, body: str, span: SourceSpan) -> SourceRef:
    return SourceRef(
        source_id="knowledge-annotation-test",
        revision="0" * 40,
        relative_path=relative_path,
        anchor=f"L{span.start_line}-L{span.end_line}",
        authority="test_fixture",
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )


def _clause(text: str, *, rule_id: str = "TEST/TIMER/R-01") -> ExtractedClause:
    span = SourceSpan(start_line=3, end_line=3)
    source_ref = _source_ref("timer-rule.md", text, span)
    return ExtractedClause(
        rule_id=rule_id,
        proposed_status="Draft",
        candidate=ClauseCandidate.create(
            native_rule_id=None,
            rule_type="constraint",
            text=text,
            heading_path=("定时器资源",),
            parent_context=None,
            neighbor_candidate_ids=(),
            applicability=Applicability(),
            source_ref=source_ref,
            source_span=span,
        ),
    )


def _api_symbol(
    signature: str,
    *,
    start_line: int,
) -> ApiSymbol:
    span = SourceSpan(start_line=start_line, end_line=start_line)
    source_ref = _source_ref("api/@ohos.events.emitter.d.ts", signature, span)
    return ApiSymbol.create(
        canonical_name="emitter.on",
        module="@ohos.events.emitter",
        kind="function",
        signature=signature,
        since=7,
        deprecated_since=None,
        source_ref=source_ref,
        source_span=span,
        catalog_version="api-catalog-test-v1",
    )


def _configs() -> tuple[FeatureConfig, KnowledgeAnnotationConfig]:
    features = load_default_feature_config()
    return features, load_knowledge_annotation_config(feature_config=features)


def test_clause_dimensions_require_real_tag_signal_and_exclude_always_check() -> None:
    features, config = _configs()
    annotation = annotate_clause(
        _clause("组件创建周期定时器后，应调用 `clearInterval` 主动清理。"),
        catalog={},
        feature_config=features,
        config=config,
        index_version="candidate:test",
    )

    assert annotation.tags == ("has_timer",)
    assert annotation.dimension_ids == ("DIM-06",)
    assert not {
        "DIM-01",
        "DIM-02",
        "DIM-03",
        "DIM-04",
        "DIM-05",
        "DIM-12",
    }.intersection(annotation.dimension_ids)


def test_source_domain_is_registered_and_has_source_metadata_provenance() -> None:
    features, config = _configs()
    annotation = annotate_clause(
        _clause("组件创建周期定时器后，应调用 `clearInterval` 主动清理。"),
        catalog={},
        feature_config=features,
        config=config,
        index_version="candidate:test",
        source_domains=("timer-subscription-lifecycle",),
    )

    assert "timer-subscription-lifecycle" in annotation.domains
    provenance = [
        item
        for item in annotation.provenance
        if item.kind == "domain" and item.value == "timer-subscription-lifecycle"
    ]
    assert [
        (item.origin, item.evidence_ref) for item in provenance
    ] == [("source_metadata", "source-domain:timer-subscription-lifecycle")]

    with pytest.raises(ValueError, match="unregistered source domains"):
        annotate_clause(
            _clause("组件创建周期定时器后，应调用 `clearInterval` 主动清理。"),
            catalog={},
            feature_config=features,
            config=config,
            index_version="candidate:test",
            source_domains=("unregistered-domain",),
        )


def test_overloaded_api_symbols_keep_declaration_identity_and_self_provenance() -> None:
    features, config = _configs()
    first = _api_symbol("function on(eventId: number): void", start_line=5)
    second = _api_symbol("function on(eventId: string): void", start_line=8)
    catalog = {first.canonical_name: first}

    first_annotation = annotate_api_symbol(
        first,
        catalog=catalog,
        feature_config=features,
        config=config,
        index_version="candidate:test",
    )
    second_annotation = annotate_api_symbol(
        second,
        catalog=catalog,
        feature_config=features,
        config=config,
        index_version="candidate:test",
    )

    assert first.declaration_id != second.declaration_id
    assert first_annotation.target_id == first.declaration_id
    assert second_annotation.target_id == second.declaration_id
    assert first_annotation.target_id != second_annotation.target_id
    for annotation in (first_annotation, second_annotation):
        self_provenance = [
            item
            for item in annotation.provenance
            if item.kind == "api" and item.value == "emitter.on"
        ]
        assert [item.evidence_ref for item in self_provenance] == [annotation.target_id]


@pytest.mark.skipif(
    not REAL_EXTRACTION.is_file(),
    reason="real knowledge-seed-v1 extraction artifact is unavailable",
)
def test_real_annotation_artifact_is_complete_repeatable_and_collision_safe() -> None:
    extraction = KnowledgeExtractionBuild.model_validate_json(
        REAL_EXTRACTION.read_text(encoding="utf-8")
    )
    features, config = _configs()

    first = build_knowledge_annotations(
        extraction,
        feature_config=features,
        config=config,
    )
    second = build_knowledge_annotations(
        extraction,
        feature_config=features,
        config=config,
    )

    clause_count = sum(len(document.clauses) for document in extraction.documents)
    api_count = sum(len(document.api_symbols) for document in extraction.documents)
    assert (clause_count, api_count) == (314, 644)
    assert first == second
    assert first.build_id == second.build_id
    assert len(first.annotations) == clause_count + api_count
    target_keys = [
        (annotation.target_kind, annotation.target_id)
        for annotation in first.annotations
    ]
    assert target_keys == sorted(set(target_keys))

    symbols_by_id = {
        symbol.declaration_id: symbol
        for document in extraction.documents
        for symbol in document.api_symbols
    }
    overloaded_names = {
        symbol.canonical_name
        for symbol in symbols_by_id.values()
        if sum(
            candidate.canonical_name == symbol.canonical_name
            for candidate in symbols_by_id.values()
        )
        > 1
    }
    assert overloaded_names
    overloaded_annotations = [
        annotation
        for annotation in first.annotations
        if annotation.target_kind == "api_symbol"
        and symbols_by_id[annotation.target_id].canonical_name in overloaded_names
    ]
    assert overloaded_annotations
    assert all(
        any(
            provenance.kind == "api"
            and provenance.value == symbols_by_id[annotation.target_id].canonical_name
            and provenance.evidence_ref == annotation.target_id
            for provenance in annotation.provenance
        )
        for annotation in overloaded_annotations
    )
