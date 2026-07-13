from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

from arkts_code_reviewer.code_analysis import CodeAnalyzer, LexicalParser
from arkts_code_reviewer.code_analysis.change_set import (
    ChangeAtomInput,
    ChangedFileInput,
    CodeSourceSnapshot,
    normalize_change_set,
)
from arkts_code_reviewer.code_analysis.context_planning import (
    ContextPlanner,
    ContextPlanResult,
    QuestionBinding,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import CodeSourceRef
from arkts_code_reviewer.code_analysis.models import (
    AnalysisResult,
    RetrievalQuery,
    ReviewUnitSpan,
)
from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    AnnotationKind,
    AnnotationProvenance,
    ApiSymbol,
    Applicability,
    CurationDecision,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.knowledge.publication import (
    PublishedClause,
    PublishedKnowledgeBuild,
)
from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.assembler import assemble_unit_evidence
from arkts_code_reviewer.retrieval.config import (
    DEFAULT_RETRIEVAL_CONFIG_PATH,
    RetrievalConfig,
    load_default_retrieval_config,
    load_retrieval_config,
)
from arkts_code_reviewer.retrieval.embeddings import FastEmbedProvider, _cache_fingerprint
from arkts_code_reviewer.retrieval.exact import ExactHit, search_exact
from arkts_code_reviewer.retrieval.fusion import FusedHit, fuse_hits
from arkts_code_reviewer.retrieval.index import (
    build_knowledge_index,
    estimate_knowledge_tokens,
)
from arkts_code_reviewer.retrieval.models import (
    EvidenceMatch,
    EvidencePack,
    KnowledgeIndex,
    KnowledgeIndexRecord,
    ParserContextQuality,
    RetrievalRequest,
    RetrievalUnitRequest,
    TargetPlatform,
    UnitExactSignals,
    load_evidence_pack,
    load_knowledge_index,
    load_retrieval_request,
)
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.service import RetrievalService
from arkts_code_reviewer.retrieval.vector import (
    VectorHit,
    query_embedding_text,
    search_vector,
)

_NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
_CURATION_VERSION = f"knowledge-curation:sha256:{'c' * 64}"
_SOURCE_INDEX_VERSION = "source-annotation-index-v1"


class _FakeEmbeddingProvider:
    def __init__(
        self,
        *,
        model_id: str = "fixture-embedding",
        version: str = "fixture-embedding-v1",
        dimensions: int = 2,
        query_vector: tuple[float, ...] = (1.0, 0.0),
        passage_vectors: tuple[tuple[float, ...], ...] = (),
        query_error: Exception | None = None,
    ) -> None:
        self._model_id = model_id
        self._version = version
        self._dimensions = dimensions
        self.query_vector = query_vector
        self.passage_vectors = passage_vectors
        self.query_error = query_error
        self.query_calls = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if self.passage_vectors:
            return self.passage_vectors
        return tuple(self.query_vector for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        self.query_calls += 1
        if self.query_error is not None:
            raise self.query_error
        return self.query_vector


def _source_ref(rule_id: str, *, authority: str = "feature_spec") -> SourceRef:
    safe_name = rule_id.replace("/", "-")
    return SourceRef(
        source_id=f"source-{safe_name}",
        revision="a" * 40,
        relative_path=f"rules/{safe_name}.md",
        anchor="L1-L2",
        authority=authority,
        content_hash=f"sha256:{'b' * 64}",
    )


def _annotation(
    rule_id: str,
    *,
    index_version: str = _SOURCE_INDEX_VERSION,
    dimension_ids: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    apis: tuple[str, ...] = (),
    components: tuple[str, ...] = (),
    decorators: tuple[str, ...] = (),
    domains: tuple[str, ...] = ("resource-management",),
    raw_keywords: tuple[str, ...] = (),
    llm_keywords: tuple[str, ...] = (),
    scenario: str | None = None,
) -> KnowledgeAnnotation:
    annotated: tuple[tuple[AnnotationKind, tuple[str, ...]], ...] = (
        ("api", apis),
        ("component", components),
        ("decorator", decorators),
        ("dimension", dimension_ids),
        ("domain", domains),
        ("keyword", tuple(sorted(set((*raw_keywords, *llm_keywords))))),
        ("tag", tags),
    )
    provenance = [
        AnnotationProvenance(
            kind=kind,
            value=value,
            origin="human_curator",
            evidence_ref=f"fixture:{kind}:{value}",
        )
        for kind, values in annotated
        for value in values
    ]
    if scenario is not None:
        provenance.append(
            AnnotationProvenance(
                kind="scenario",
                value=scenario,
                origin="human_curator",
                evidence_ref=f"fixture:scenario:{scenario}",
            )
        )
    return KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version=index_version,
        dimension_ids=dimension_ids,
        tags=tags,
        apis=apis,
        components=components,
        decorators=decorators,
        domains=domains,
        raw_keywords=raw_keywords,
        llm_keywords=llm_keywords,
        scenario=scenario,
        provenance=tuple(
            sorted(
                provenance,
                key=lambda item: (item.kind, item.value, item.origin, item.evidence_ref),
            )
        ),
        annotation_version="annotation-v1",
    )


def _clause(
    rule_id: str,
    *,
    status: Literal["Draft", "Baselined", "Deprecated"] = "Baselined",
    authority: str = "feature_spec",
    applicability: Applicability | None = None,
    text: str | None = None,
) -> KnowledgeClause:
    source_ref = _source_ref(rule_id, authority=authority)
    return KnowledgeClause(
        rule_id=rule_id,
        rule_type="RULE",
        status=status,
        authority=authority,
        text=text or f"Normative rule {rule_id}.",
        heading_path=("Rules", rule_id),
        parent_context="Fixture parent context.",
        applicability=applicability or Applicability(),
        source_ref=source_ref,
        source_span=SourceSpan(start_line=1, end_line=2),
        doc_hash=source_ref.content_hash,
        curation_version=_CURATION_VERSION,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _record(
    rule_id: str,
    *,
    status: Literal["Draft", "Baselined", "Deprecated"] = "Baselined",
    authority: str = "feature_spec",
    applicability: Applicability | None = None,
    dimension_ids: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    apis: tuple[str, ...] = (),
    components: tuple[str, ...] = (),
    decorators: tuple[str, ...] = (),
    raw_keywords: tuple[str, ...] = (),
    embedding: tuple[float, ...] | None = None,
    token_count: int = 8,
) -> KnowledgeIndexRecord:
    return KnowledgeIndexRecord(
        clause=_clause(
            rule_id,
            status=status,
            authority=authority,
            applicability=applicability,
        ),
        annotation=_annotation(
            rule_id,
            dimension_ids=dimension_ids,
            tags=tags,
            apis=apis,
            components=components,
            decorators=decorators,
            raw_keywords=raw_keywords,
        ),
        domains=("resource-management",),
        retrieval_text=f"Scenario and normative text for {rule_id}.",
        token_count=token_count,
        embedding=embedding,
    )


def _api_symbol(
    canonical_name: str,
    *,
    aliases: tuple[str, ...] = (),
) -> ApiSymbol:
    source_ref = _source_ref(f"api-{canonical_name}", authority="official_api_definition")
    return ApiSymbol.create(
        canonical_name=canonical_name,
        aliases=aliases,
        module="@fixture/api",
        kind="function",
        signature=f"{canonical_name}(): void",
        since=1,
        deprecated_since=None,
        source_ref=source_ref,
        source_span=SourceSpan(start_line=1, end_line=1),
        catalog_version="api-catalog-v1",
    )


def _index(
    records: tuple[KnowledgeIndexRecord, ...],
    *,
    api_symbols: tuple[ApiSymbol, ...] = (),
    embedded: bool | None = None,
) -> KnowledgeIndex:
    retrieval_config = load_default_retrieval_config()
    has_embeddings = (
        any(record.embedding is not None for record in records)
        if embedded is None
        else embedded
    )
    return KnowledgeIndex.create(
        origin="golden_fixture",
        published_build_id=f"retrieval-fixture:sha256:{'f' * 64}",
        source_bundle_id=f"source-bundle:sha256:{'d' * 64}",
        feature_config_version=load_default_feature_config().fingerprint,
        annotation_version="annotation-v1",
        catalog_version="api-catalog-v1",
        retrieval_version=retrieval_config.version,
        retrieval_config_fingerprint=retrieval_config.fingerprint,
        embedding_model="fixture-embedding" if has_embeddings else None,
        embedding_version="fixture-embedding-v1" if has_embeddings else None,
        embedding_dimensions=2 if has_embeddings else None,
        api_symbols=api_symbols,
        records=records,
    )


def _unit(
    unit_id: str = "unit-a",
    *,
    exact_signals: UnitExactSignals | None = None,
    exact_tags: tuple[str, ...] = (),
    routing_tags: tuple[str, ...] = (),
    retrieval_dimensions: tuple[str, ...] = (),
    routing_dimensions: tuple[str, ...] | None = None,
    requested_rule_ids: tuple[str, ...] = (),
    dispatchable: tuple[str, ...] | None = None,
    budget: int = 64,
    intent_summary: str = "ArkTS review unit",
    semantic_code_excerpt: str | None = None,
) -> RetrievalUnitRequest:
    questions = ("RQ-correctness",)
    return RetrievalUnitRequest(
        unit_id=unit_id,
        source_ref_id=f"code-source:sha256:{'1' * 64}",
        profile_id=f"feature-profile:sha256:{'2' * 64}",
        review_question_ids=questions,
        dispatchable_review_question_ids=(questions if dispatchable is None else dispatchable),
        exact_signals=exact_signals or UnitExactSignals(),
        exact_tags=exact_tags,
        routing_tags=routing_tags,
        retrieval_dimension_ids=retrieval_dimensions,
        routing_dimension_ids=(
            retrieval_dimensions if routing_dimensions is None else routing_dimensions
        ),
        requested_rule_ids=requested_rule_ids,
        semantic_code_excerpt=semantic_code_excerpt,
        intent_summary=intent_summary,
        quality=ParserContextQuality(
            parser_layer="L1",
            context_degraded=False,
            error_nodes=0,
            missing_nodes=0,
        ),
        knowledge_token_budget=budget,
    )


def _request(index: KnowledgeIndex, units: tuple[RetrievalUnitRequest, ...]) -> RetrievalRequest:
    return RetrievalRequest.create(
        context_plan_id=f"context-plan:sha256:{'3' * 64}",
        feature_routing_id=f"feature-routing:sha256:{'4' * 64}",
        feature_config_version=load_default_feature_config().fingerprint,
        index_version=index.index_version,
        target_platform=TargetPlatform(),
        total_knowledge_token_budget=sum(item.knowledge_token_budget for item in units),
        units=units,
    )


def _publication(
    records: tuple[KnowledgeIndexRecord, ...],
    *,
    api_symbols: tuple[ApiSymbol, ...] = (),
) -> PublishedKnowledgeBuild:
    clauses = tuple(
        PublishedClause(
            clause=record.clause,
            annotation=record.annotation,
            domains=record.domains,
        )
        for record in records
    )
    decisions = tuple(
        CurationDecision(
            rule_id=record.clause.rule_id,
            content_hash=f"sha256:{'e' * 64}",
            content_decision="approved",
            annotation_decision="approved",
            reviewer_kind="human",
            reviewer_id="fixture-human",
            review_version="fixture-review-v1",
        )
        for record in records
    )
    draft = PublishedKnowledgeBuild.model_construct(
        build_id=f"published-knowledge:sha256:{'0' * 64}",
        packet_build_id=f"knowledge-review-packets:sha256:{'1' * 64}",
        consensus_build_id=(
            f"knowledge-review-consensus-build:sha256:{'2' * 64}"
        ),
        extraction_build_id=f"knowledge-extraction:sha256:{'3' * 64}",
        annotation_build_id=f"knowledge-annotation:sha256:{'4' * 64}",
        source_bundle_id=f"source-bundle:sha256:{'5' * 64}",
        feature_config_fingerprint=load_default_feature_config().fingerprint,
        annotation_config_fingerprint=f"annotation-config:sha256:{'6' * 64}",
        annotation_version="annotation-v1",
        source_annotation_index_version=_SOURCE_INDEX_VERSION,
        curation_version=_CURATION_VERSION,
        published_at=_NOW,
        curation_decisions=decisions,
        clauses=clauses,
        api_symbols=api_symbols,
    )
    return PublishedKnowledgeBuild(
        build_id=draft.expected_build_id(),
        packet_build_id=f"knowledge-review-packets:sha256:{'1' * 64}",
        consensus_build_id=(
            f"knowledge-review-consensus-build:sha256:{'2' * 64}"
        ),
        extraction_build_id=f"knowledge-extraction:sha256:{'3' * 64}",
        annotation_build_id=f"knowledge-annotation:sha256:{'4' * 64}",
        source_bundle_id=f"source-bundle:sha256:{'5' * 64}",
        feature_config_fingerprint=load_default_feature_config().fingerprint,
        annotation_config_fingerprint=f"annotation-config:sha256:{'6' * 64}",
        annotation_version="annotation-v1",
        source_annotation_index_version=_SOURCE_INDEX_VERSION,
        curation_version=_CURATION_VERSION,
        published_at=_NOW,
        curation_decisions=decisions,
        clauses=clauses,
        api_symbols=api_symbols,
    )


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    inline = CodeSourceRef.inline(path, content, repository="retrieval-test")
    source_ref = CodeSourceRef.create(
        repository=inline.repository,
        revision=revision,
        path=inline.path,
        content_hash=inline.content_hash,
    )
    return CodeSourceSnapshot(source_ref=source_ref, content=content)


def _analysis_and_context(
    *,
    path: str = "src/A.ets",
    code_context_budget: int = 1000,
) -> tuple[AnalysisResult, ContextPlanResult, tuple[CodeSourceSnapshot, ...]]:
    base = _snapshot(
        path,
        "function changed() {\n  setInterval(() => {}, 10)\n}\n",
        "base",
    )
    head = _snapshot(
        path,
        "function changed() {\n  clearInterval(timer)\n}\n",
        "head",
    )
    change_set = normalize_change_set(
        repository="retrieval-test",
        base_revision="base",
        head_revision="head",
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(2, 2),
                        new_span=ReviewUnitSpan(2, 2),
                        added_new_lines=(2,),
                        deleted_old_lines=(2,),
                    ),
                ),
            ),
        ),
    )
    snapshots = (base, head)
    analyzer = CodeAnalyzer(parser=LexicalParser())
    analysis = analyzer.analyze_change_set(
        change_set,
        {item.source_ref.source_ref_id: item for item in snapshots},
    )
    context = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=code_context_budget,
    )
    return analysis, context, snapshots


def _write_config(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "retrieval.yaml"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _add_unknown_config_key(payload: dict[str, object]) -> None:
    payload["unknown_key"] = True


def _add_unknown_weight(payload: dict[str, object]) -> None:
    weights = cast("dict[str, object]", payload["weights"])
    weights["unknown_weight"] = 1


def test_default_config_loads_with_stable_fingerprint() -> None:
    first = load_retrieval_config(DEFAULT_RETRIEVAL_CONFIG_PATH)
    second = load_default_retrieval_config()

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint.startswith("retrieval-config:sha256:")


def test_token_estimate_is_conservative_for_cjk_and_mixed_text() -> None:
    assert estimate_knowledge_tokens("abcd") == 1
    assert estimate_knowledge_tokens("中文") == 2
    assert estimate_knowledge_tokens("abcd中文") == 3


def test_config_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    raw = DEFAULT_RETRIEVAL_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "retrieval.yaml"
    path.write_text(f"{raw}\nmax_units: 12\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_retrieval_config(path)


@pytest.mark.parametrize(
    "mutate",
    [
        _add_unknown_config_key,
        _add_unknown_weight,
    ],
)
def test_config_rejects_unknown_fields(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    payload = load_default_retrieval_config().model_dump(mode="json")
    mutate(payload)

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_retrieval_config(_write_config(tmp_path, payload))


def test_config_rejects_duplicate_authority(tmp_path: Path) -> None:
    payload = load_default_retrieval_config().model_dump(mode="json")
    priorities = cast("list[dict[str, object]]", payload["authority_priorities"])
    priorities[-1]["authority"] = priorities[0]["authority"]

    with pytest.raises(ValueError, match="unique"):
        load_retrieval_config(_write_config(tmp_path, payload))


def test_config_rejects_unstable_authority_order(tmp_path: Path) -> None:
    payload = load_default_retrieval_config().model_dump(mode="json")
    priorities = cast("list[dict[str, object]]", payload["authority_priorities"])
    priorities[0], priorities[1] = priorities[1], priorities[0]

    with pytest.raises(ValueError, match="stable priority order"):
        load_retrieval_config(_write_config(tmp_path, payload))


def test_config_rejects_weight_priority_drift(tmp_path: Path) -> None:
    payload = load_default_retrieval_config().model_dump(mode="json")
    weights = cast("dict[str, object]", payload["weights"])
    weights["api"] = weights["component"]

    with pytest.raises(ValueError, match="priority order"):
        load_retrieval_config(_write_config(tmp_path, payload))


def test_config_strictly_rejects_boolean_integer(tmp_path: Path) -> None:
    payload = load_default_retrieval_config().model_dump(mode="json")
    payload["max_units"] = True

    with pytest.raises(ValueError, match="valid integer"):
        load_retrieval_config(_write_config(tmp_path, payload))


def test_unit_model_rejects_unsorted_and_duplicate_sequences() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        _unit(exact_tags=("has_timer", "has_async"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exact_tags", ("not-a-tag",)),
        ("retrieval_dimension_ids", ("DIM-99",)),
        ("review_question_ids", ("RQ-missing",)),
    ],
)
def test_unit_model_rejects_unknown_registry_ids(field: str, value: tuple[str, ...]) -> None:
    payload = _unit().model_dump(mode="python")
    payload[field] = value
    if field == "retrieval_dimension_ids":
        payload["routing_dimension_ids"] = value
    if field == "review_question_ids":
        payload["dispatchable_review_question_ids"] = value

    with pytest.raises(ValueError, match="unregistered"):
        RetrievalUnitRequest.model_validate(payload)


def test_unit_model_requires_retrieval_dimensions_to_be_routable() -> None:
    with pytest.raises(ValueError, match="subset"):
        _unit(
            retrieval_dimensions=("DIM-06",),
            routing_dimensions=("DIM-11",),
        )


def test_request_factory_is_identity_stable_across_unit_input_order() -> None:
    index = _index((_record("R-1"),))
    first = _unit("unit-a", budget=10)
    second = _unit("unit-b", budget=11)

    left = _request(index, (first, second))
    right = _request(index, (second, first))

    assert left == right
    assert tuple(item.unit_id for item in left.units) == ("unit-a", "unit-b")


def test_request_loader_rejects_duplicate_json_key() -> None:
    request = _request(_index((_record("R-1"),)), (_unit(),))
    raw = request.model_dump_json()
    duplicated = '{"schema_version":"retrieval-request-v1",' + raw[1:]

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_request(duplicated)


def test_request_loader_rejects_unknown_field() -> None:
    request = _request(_index((_record("R-1"),)), (_unit(),))
    payload = request.model_dump(mode="json")
    payload["unknown"] = "forbidden"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_retrieval_request(json.dumps(payload))


def test_request_loader_rejects_boolean_budget() -> None:
    request = _request(_index((_record("R-1"),)), (_unit(),))
    payload = request.model_dump(mode="json")
    payload["total_knowledge_token_budget"] = True

    with pytest.raises(ValueError, match="valid integer"):
        load_retrieval_request(json.dumps(payload))


def test_request_loader_rejects_noncanonical_unit_order() -> None:
    index = _index((_record("R-1"),))
    request = _request(index, (_unit("unit-a"), _unit("unit-b")))
    payload = request.model_dump(mode="json")
    units = cast("list[object]", payload["units"])
    units.reverse()

    with pytest.raises(ValueError, match="stably sorted"):
        load_retrieval_request(json.dumps(payload))


def test_request_loader_rejects_identity_tampering() -> None:
    request = _request(_index((_record("R-1"),)), (_unit(),))
    payload = request.model_dump(mode="json")
    payload["context_plan_id"] = f"context-plan:sha256:{'9' * 64}"

    with pytest.raises(ValueError, match="request_id does not match"):
        load_retrieval_request(json.dumps(payload))


def test_index_record_accepts_only_baselined_clause() -> None:
    with pytest.raises(ValueError, match="only Baselined"):
        _record("R-DRAFT", status="Draft")


def test_index_factory_normalizes_records_and_api_symbol_order() -> None:
    first_record = _record("R-1")
    second_record = _record("R-2")
    first_symbol = _api_symbol("alpha", aliases=("a",))
    second_symbol = _api_symbol("zeta", aliases=("z",))

    left = _index(
        (second_record, first_record),
        api_symbols=(second_symbol, first_symbol),
    )
    right = _index(
        (first_record, second_record),
        api_symbols=(first_symbol, second_symbol),
    )

    assert left == right
    assert tuple(item.clause.rule_id for item in left.records) == ("R-1", "R-2")
    assert tuple(item.canonical_name for item in left.api_symbols) == ("alpha", "zeta")


def test_index_rebinds_annotation_self_reference_without_hash_cycle() -> None:
    index = _index((_record("R-1"), _record("R-2")))

    assert all(
        item.annotation.index_version == index.index_version for item in index.records
    )
    assert load_knowledge_index(index.model_dump_json()) == index


def test_index_loader_rejects_annotation_self_reference_tampering() -> None:
    index = _index((_record("R-1"),))
    payload = index.model_dump(mode="json")
    records = cast("list[dict[str, object]]", payload["records"])
    annotation = cast("dict[str, object]", records[0]["annotation"])
    annotation["index_version"] = f"knowledge-index:sha256:{'9' * 64}"

    with pytest.raises(ValueError, match="annotation does not match index"):
        load_knowledge_index(json.dumps(payload))


def test_index_loader_rejects_identity_tampering() -> None:
    index = _index((_record("R-1"),))
    payload = index.model_dump(mode="json")
    payload["retrieval_version"] = "retrieval-v2"

    with pytest.raises(ValueError, match="index_version does not match"):
        load_knowledge_index(json.dumps(payload))


def test_index_rejects_partial_embedding_metadata() -> None:
    record = _record("R-1")
    config = load_default_retrieval_config()

    with pytest.raises(ValueError, match="all present or all absent"):
        KnowledgeIndex.create(
            origin="golden_fixture",
            published_build_id=f"retrieval-fixture:sha256:{'f' * 64}",
            source_bundle_id=f"source-bundle:sha256:{'d' * 64}",
            feature_config_version=load_default_feature_config().fingerprint,
            annotation_version="annotation-v1",
            catalog_version="api-catalog-v1",
            retrieval_version=config.version,
            retrieval_config_fingerprint=config.fingerprint,
            embedding_model="fixture-embedding",
            embedding_version=None,
            embedding_dimensions=2,
            api_symbols=(),
            records=(record,),
        )


def test_evidence_pack_loader_rejects_duplicate_key_and_unknown_field() -> None:
    index = _index((_record("R-1", apis=("foo",)),))
    request = _request(
        index,
        (_unit(exact_signals=UnitExactSignals(apis=("foo",))),),
    )
    pack = RetrievalService(index, allow_golden_fixture=True).retrieve(request)
    raw = pack.model_dump_json()
    duplicate = '{"schema_version":"evidence-pack-v1",' + raw[1:]

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_evidence_pack(duplicate)
    payload = pack.model_dump(mode="json")
    payload["unknown"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_evidence_pack(json.dumps(payload))


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (
            TargetPlatform(
                release="OpenHarmony-5.0",
                api_level=12,
                language_mode="static",
                permissions=("ohos.permission.INTERNET",),
                system_capabilities=("SystemCapability.Communication.NetManager",),
            ),
            "applicable",
        ),
        (TargetPlatform(), "unknown"),
        (
            TargetPlatform(
                release="OpenHarmony-4.0",
                api_level=9,
                language_mode="dynamic",
                permissions=(),
                system_capabilities=(),
            ),
            "excluded",
        ),
    ],
)
def test_applicability_distinguishes_known_unknown_and_excluded(
    target: TargetPlatform,
    expected: str,
) -> None:
    applicability = Applicability(
        min_api_level=10,
        max_api_level=14,
        releases=("OpenHarmony-5.0",),
        language_modes=("static",),
        permissions=("ohos.permission.INTERNET",),
        system_capabilities=("SystemCapability.Communication.NetManager",),
    )

    result = evaluate_applicability(applicability, target)

    assert result.decision == expected
    assert result.reasons == tuple(sorted(set(result.reasons)))
    if expected == "applicable":
        assert result.reasons == ()
    else:
        assert result.reasons


def test_applicability_exclusion_dominates_other_unknown_fields() -> None:
    result = evaluate_applicability(
        Applicability(min_api_level=12, releases=("OpenHarmony-5.0",)),
        TargetPlatform(api_level=10),
    )

    assert result.decision == "excluded"
    assert result.reasons == ("api_level_below_minimum",)


def test_exact_search_resolves_unambiguous_api_alias_to_canonical_name() -> None:
    record = _record("R-API", apis=("router.pushUrl",))
    index = _index(
        (record,),
        api_symbols=(_api_symbol("router.pushUrl", aliases=("pushUrl",)),),
    )
    unit = _unit(exact_signals=UnitExactSignals(apis=("pushUrl",)))

    hits = search_exact(index, unit, TargetPlatform(), load_default_retrieval_config())

    assert [item.record.clause.rule_id for item in hits] == ["R-API"]
    assert hits[0].matched_by == (
        EvidenceMatch(kind="api", value="router.pushUrl", scope="unit_exact"),
    )


@pytest.mark.parametrize(
    ("signal", "annotation_field", "kind"),
    [
        ("Button", "components", "component"),
        ("@State", "decorators", "decorator"),
    ],
)
def test_exact_search_matches_component_and_decorator(
    signal: str,
    annotation_field: str,
    kind: Literal["component", "decorator"],
) -> None:
    if annotation_field == "components":
        record = _record("R-STRUCTURED", components=(signal,))
        signals = UnitExactSignals(components=(signal,))
    else:
        record = _record("R-STRUCTURED", decorators=(signal,))
        signals = UnitExactSignals(decorators=(signal,))

    hits = search_exact(
        _index((record,)),
        _unit(exact_signals=signals),
        TargetPlatform(),
        load_default_retrieval_config(),
    )

    assert hits[0].matched_by == (
        EvidenceMatch(kind=kind, value=signal, scope="unit_exact"),
    )


def test_exact_tag_outranks_file_hint_and_is_not_double_counted() -> None:
    index = _index((_record("R-TAG", tags=("has_timer",)),))
    config = load_default_retrieval_config()
    exact = search_exact(
        index,
        _unit(exact_tags=("has_timer",), routing_tags=("has_timer",)),
        TargetPlatform(),
        config,
    )[0]
    hint = search_exact(
        index,
        _unit(routing_tags=("has_timer",)),
        TargetPlatform(),
        config,
    )[0]

    assert exact.score - config.weights.applicability_exact == config.weights.exact_tag
    assert hint.score - config.weights.applicability_exact == config.weights.routing_tag
    assert exact.score > hint.score
    assert exact.matched_by == (
        EvidenceMatch(kind="tag", value="has_timer", scope="unit_exact"),
    )
    assert hint.matched_by == (
        EvidenceMatch(kind="tag", value="has_timer", scope="file_hint"),
    )


def test_exact_search_never_recalls_from_dimension_alone() -> None:
    index = _index((_record("R-DIM", dimension_ids=("DIM-06",)),))
    unit = _unit(retrieval_dimensions=("DIM-06",))

    assert search_exact(
        index,
        unit,
        TargetPlatform(),
        load_default_retrieval_config(),
    ) == ()


def test_exact_search_filters_excluded_and_retains_unknown_applicability() -> None:
    index = _index(
        (
            _record(
                "R-EXCLUDED",
                apis=("foo",),
                applicability=Applicability(min_api_level=12),
            ),
            _record(
                "R-UNKNOWN",
                apis=("foo",),
                applicability=Applicability(releases=("OpenHarmony-5.0",)),
            ),
        )
    )
    unit = _unit(exact_signals=UnitExactSignals(apis=("foo",)))

    hits = search_exact(
        index,
        unit,
        TargetPlatform(api_level=10),
        load_default_retrieval_config(),
    )

    assert [item.record.clause.rule_id for item in hits] == ["R-UNKNOWN"]
    assert hits[0].applicability == "unknown"


def test_exact_search_tie_order_is_rule_id_stable_across_record_input_order() -> None:
    first = _record("R-A", apis=("foo",))
    second = _record("R-B", apis=("foo",))
    unit = _unit(exact_signals=UnitExactSignals(apis=("foo",)))
    config = load_default_retrieval_config()

    left = search_exact(_index((first, second)), unit, TargetPlatform(), config)
    right = search_exact(_index((second, first)), unit, TargetPlatform(), config)

    assert [item.record.clause.rule_id for item in left] == ["R-A", "R-B"]
    assert left == right


def test_exact_keyword_can_recover_from_changed_code_when_parser_has_no_fact() -> None:
    index = _index((_record("R-KEYWORD", raw_keywords=("setInterval",)),))
    unit = _unit(semantic_code_excerpt="L2: setInterval(() => {}, 1000)")

    hits = search_exact(
        index,
        unit,
        TargetPlatform(),
        load_default_retrieval_config(),
    )

    assert [item.record.clause.rule_id for item in hits] == ["R-KEYWORD"]
    assert hits[0].matched_by == (
        EvidenceMatch(kind="keyword", value="setInterval", scope="unit_exact"),
    )


def test_vector_search_skips_dimension_only_request_without_embedding_call() -> None:
    index = _index(
        (_record("R-DIM", dimension_ids=("DIM-06",), embedding=(1.0, 0.0)),)
    )
    provider = _FakeEmbeddingProvider()

    hits = search_vector(
        index,
        _unit(retrieval_dimensions=("DIM-06",)),
        TargetPlatform(),
        load_default_retrieval_config(),
        provider,
    )

    assert hits == ()
    assert provider.query_calls == 0


def test_vector_search_accepts_changed_code_as_a_real_semantic_signal() -> None:
    index = _index((_record("R-VECTOR", embedding=(1.0, 0.0)),))
    provider = _FakeEmbeddingProvider(query_vector=(1.0, 0.0))
    unit = _unit(
        intent_summary="ArkTS fallback change",
        semantic_code_excerpt="L2: clearInterval(timer)",
    )

    hits = search_vector(
        index,
        unit,
        TargetPlatform(),
        load_default_retrieval_config(),
        provider,
    )

    assert [item.record.clause.rule_id for item in hits] == ["R-VECTOR"]
    assert provider.query_calls == 1


@pytest.mark.parametrize(
    "provider",
    [
        _FakeEmbeddingProvider(version="fixture-embedding-v2"),
        _FakeEmbeddingProvider(dimensions=3, query_vector=(1.0, 0.0, 0.0)),
    ],
)
def test_vector_search_requires_exact_provider_version_and_dimensions(
    provider: _FakeEmbeddingProvider,
) -> None:
    index = _index((_record("R-VECTOR", embedding=(1.0, 0.0)),))
    unit = _unit(exact_signals=UnitExactSignals(apis=("foo",)))

    with pytest.raises(ValueError, match="does not match"):
        search_vector(
            index,
            unit,
            TargetPlatform(),
            load_default_retrieval_config(),
            provider,
        )


def test_vector_similarity_threshold_is_inclusive_and_configurable() -> None:
    vector = (0.5, math.sqrt(0.75))
    index = _index((_record("R-VECTOR", embedding=vector),))
    unit = _unit(exact_signals=UnitExactSignals(apis=("foo",)))
    provider = _FakeEmbeddingProvider(query_vector=(1.0, 0.0))
    payload = load_default_retrieval_config().model_dump(mode="python")
    payload["minimum_vector_similarity"] = 0.5
    inclusive = RetrievalConfig.model_validate(payload)
    payload["minimum_vector_similarity"] = 0.50000001
    exclusive = RetrievalConfig.model_validate(payload)

    assert len(search_vector(index, unit, TargetPlatform(), inclusive, provider)) == 1
    assert search_vector(index, unit, TargetPlatform(), exclusive, provider) == ()


def test_vector_search_rejects_invalid_query_vector() -> None:
    index = _index((_record("R-VECTOR", embedding=(1.0, 0.0)),))
    unit = _unit(exact_signals=UnitExactSignals(apis=("foo",)))

    with pytest.raises(ValueError, match="invalid query vector"):
        search_vector(
            index,
            unit,
            TargetPlatform(),
            load_default_retrieval_config(),
            _FakeEmbeddingProvider(query_vector=(float("nan"), 0.0)),
        )


def test_vector_ties_use_dimension_then_authority_then_rule_id() -> None:
    index = _index(
        (
            _record("R-A", authority="test_evidence", embedding=(1.0, 0.0)),
            _record(
                "R-B",
                authority="test_evidence",
                dimension_ids=("DIM-06",),
                embedding=(1.0, 0.0),
            ),
            _record("R-C", authority="feature_spec", embedding=(1.0, 0.0)),
        )
    )
    unit = _unit(
        exact_signals=UnitExactSignals(apis=("foo",)),
        retrieval_dimensions=("DIM-06",),
    )

    hits = search_vector(
        index,
        unit,
        TargetPlatform(),
        load_default_retrieval_config(),
        _FakeEmbeddingProvider(),
    )

    assert [item.record.clause.rule_id for item in hits] == ["R-B", "R-C", "R-A"]


def _exact_hit(
    record: KnowledgeIndexRecord,
    *,
    rank: int,
    score: int = 10,
    applicability: Literal["applicable", "unknown"] = "applicable",
) -> ExactHit:
    return ExactHit(
        record=record,
        rank=rank,
        score=score,
        matched_by=(
            EvidenceMatch(
                kind="rule_id",
                value=record.clause.rule_id,
                scope="unit_exact",
            ),
        ),
        applicability=applicability,
        dimension_overlap=0,
        authority_priority=80,
    )


def _vector_hit(
    record: KnowledgeIndexRecord,
    *,
    rank: int,
    similarity: float = 0.8,
    applicability: Literal["applicable", "unknown"] = "applicable",
) -> VectorHit:
    return VectorHit(
        record=record,
        rank=rank,
        similarity=similarity,
        matched_by=(EvidenceMatch(kind="vector", value="fixture-embedding", scope="semantic"),),
        applicability=applicability,
        dimension_overlap=0,
        authority_priority=80,
    )


def _fused_exact(record: KnowledgeIndexRecord, *, rank: int = 1) -> FusedHit:
    return fuse_hits((_exact_hit(record, rank=rank),), (), rrf_k=60)[0]


def test_rrf_fuses_both_paths_using_rank_only_formula() -> None:
    record = _record("R-BOTH")

    fused = fuse_hits(
        (_exact_hit(record, rank=1, score=999),),
        (_vector_hit(record, rank=2, similarity=0.99),),
        rrf_k=60,
    )

    assert len(fused) == 1
    assert fused[0].rrf_score == round(1 / 61 + 1 / 62, 8)
    assert fused[0].exact_rank == 1
    assert fused[0].vector_rank == 2
    assert {item.kind for item in fused[0].matched_by} == {"rule_id", "vector"}


def test_rrf_output_is_stable_under_path_input_permutations() -> None:
    first = _record("R-A")
    second = _record("R-B")
    exact = (_exact_hit(first, rank=2), _exact_hit(second, rank=1))
    vector = (_vector_hit(first, rank=1), _vector_hit(second, rank=2))

    left = fuse_hits(exact, vector, rrf_k=60)
    right = fuse_hits(tuple(reversed(exact)), tuple(reversed(vector)), rrf_k=60)

    assert left == right


@pytest.mark.parametrize("path", ["exact", "vector"])
def test_rrf_rejects_duplicate_clause_within_one_path(path: str) -> None:
    record = _record("R-DUP")
    exact = (_exact_hit(record, rank=1), _exact_hit(record, rank=2))
    vector = (_vector_hit(record, rank=1), _vector_hit(record, rank=2))

    with pytest.raises(ValueError, match="repeats a Clause"):
        fuse_hits(exact if path == "exact" else (), vector if path == "vector" else (), rrf_k=60)


def test_rrf_rejects_applicability_disagreement_between_paths() -> None:
    record = _record("R-DISAGREE")

    with pytest.raises(ValueError, match="disagree about applicability"):
        fuse_hits(
            (_exact_hit(record, rank=1, applicability="applicable"),),
            (_vector_hit(record, rank=1, applicability="unknown"),),
            rrf_k=60,
        )


def test_assembler_enforces_token_budget_and_reports_exhaustion() -> None:
    first = _record("R-A", token_count=6)
    second = _record("R-B", token_count=6)
    hits = fuse_hits(
        (_exact_hit(first, rank=1), _exact_hit(second, rank=2)),
        (),
        rrf_k=60,
    )

    evidence = assemble_unit_evidence(
        _unit(budget=10),
        hits,
        load_default_retrieval_config(),
    )

    assert [item.rule_id for item in evidence.clauses] == ["R-A"]
    assert sum(item.token_count for item in evidence.clauses) <= 10
    assert "budget_exhausted" in {item.code for item in evidence.diagnostics}


def test_assembler_coverage_counts_only_retrieval_dimensions() -> None:
    record = _record("R-ROUTING", dimension_ids=("DIM-11",))

    evidence = assemble_unit_evidence(
        _unit(
            retrieval_dimensions=("DIM-06",),
            routing_dimensions=("DIM-06", "DIM-11"),
        ),
        (_fused_exact(record),),
        load_default_retrieval_config(),
    )

    assert evidence.routing_dimension_ids == ("DIM-06", "DIM-11")
    assert evidence.covered_dimension_ids == ()
    assert evidence.uncovered_dimension_ids == ("DIM-06",)


def test_assembler_reserves_result_slots_for_each_retrieval_dimension() -> None:
    general = _record("R-GENERAL")
    resource = _record("R-RESOURCE", dimension_ids=("DIM-06",))
    security = _record("R-SECURITY", dimension_ids=("DIM-11",))
    hits = fuse_hits(
        (
            _exact_hit(general, rank=1),
            _exact_hit(resource, rank=2),
            _exact_hit(security, rank=3),
        ),
        (),
        rrf_k=60,
    )
    payload = load_default_retrieval_config().model_dump(mode="python")
    payload["result_limit"] = 2
    config = RetrievalConfig.model_validate(payload)

    evidence = assemble_unit_evidence(
        _unit(
            retrieval_dimensions=("DIM-06", "DIM-11"),
            budget=100,
        ),
        hits,
        config,
    )

    assert [item.rule_id for item in evidence.clauses] == ["R-RESOURCE", "R-SECURITY"]
    assert evidence.covered_dimension_ids == ("DIM-06", "DIM-11")
    assert evidence.uncovered_dimension_ids == ()


def test_assembler_blocks_all_evidence_when_context_dispatch_is_blocked() -> None:
    record = _record("R-BLOCKED")

    evidence = assemble_unit_evidence(
        _unit(dispatchable=()),
        (_fused_exact(record),),
        load_default_retrieval_config(),
    )

    assert evidence.clauses == ()
    assert evidence.uncovered_dimension_ids == ()
    assert {item.code for item in evidence.diagnostics} == {"context_dispatch_blocked"}


def test_service_requires_explicit_fixture_opt_in() -> None:
    index = _index((_record("R-1"),))

    with pytest.raises(ValueError, match="explicit test-only opt-in"):
        RetrievalService(index)
    with pytest.raises(TypeError, match="must be boolean"):
        RetrievalService(index, allow_golden_fixture=cast("bool", 1))


def test_service_exact_only_path_returns_a_valid_stable_evidence_pack() -> None:
    index = _index((_record("R-API", apis=("foo",)),))
    request = _request(
        index,
        (_unit(exact_signals=UnitExactSignals(apis=("foo",))),),
    )
    service = RetrievalService(index, allow_golden_fixture=True)

    first = service.retrieve(request)
    second = service.retrieve(request)

    assert first == second
    assert first.embedding_version is None
    assert first.degraded is False
    assert [item.rule_id for item in first.units[0].clauses] == ["R-API"]
    assert load_evidence_pack(first.model_dump_json()) == first


def test_service_degrades_to_exact_when_embedding_raises_os_error() -> None:
    index = _index((_record("R-API", apis=("foo",), embedding=(1.0, 0.0)),))
    request = _request(
        index,
        (_unit(exact_signals=UnitExactSignals(apis=("foo",))),),
    )
    provider = _FakeEmbeddingProvider(query_error=OSError("offline"))

    pack = RetrievalService(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    ).retrieve(request)

    assert [item.rule_id for item in pack.units[0].clauses] == ["R-API"]
    assert "embedding_unavailable" in {
        item.code for item in pack.units[0].diagnostics
    }
    assert pack.degraded is True


def test_service_rejects_index_and_active_config_fingerprint_drift() -> None:
    index = _index((_record("R-1"),))
    request = _request(index, (_unit(),))
    payload = load_default_retrieval_config().model_dump(mode="python")
    payload["minimum_vector_similarity"] = 0.31
    drifted = RetrievalConfig.model_validate(payload)

    with pytest.raises(ValueError, match="config disagree"):
        RetrievalService(
            index,
            config=drifted,
            allow_golden_fixture=True,
        ).retrieve(request)


def test_service_and_pack_keep_input_units_in_stable_identity_order() -> None:
    index = _index((_record("R-1"),))
    request = _request(index, (_unit("unit-b"), _unit("unit-a")))

    pack = RetrievalService(index, allow_golden_fixture=True).retrieve(request)
    rebuilt = EvidencePack.create(
        request_id=pack.request_id,
        retrieval_version=pack.retrieval_version,
        retrieval_config_fingerprint=pack.retrieval_config_fingerprint,
        index_version=pack.index_version,
        source_bundle_id=pack.source_bundle_id,
        embedding_version=pack.embedding_version,
        units=tuple(reversed(pack.units)),
        diagnostics=pack.diagnostics,
    )

    assert tuple(item.unit_id for item in pack.units) == ("unit-a", "unit-b")
    assert rebuilt == pack


def test_publication_index_build_binds_active_versions_and_annotation_identity() -> None:
    record = _record("R-PUBLISHED", apis=("foo",))
    publication = _publication((record,))
    config = load_default_retrieval_config()

    index = build_knowledge_index(publication, retrieval_version=config.version)

    assert index.origin == "publication"
    assert index.retrieval_config_fingerprint == config.fingerprint
    assert index.records[0].annotation.index_version == index.index_version
    assert index.records[0].clause.status == "Baselined"


@pytest.mark.parametrize(
    "provider",
    [
        _FakeEmbeddingProvider(passage_vectors=((1.0, 0.0), (0.0, 1.0))),
        _FakeEmbeddingProvider(passage_vectors=((1.0,),)),
        _FakeEmbeddingProvider(model_id=" fixture-embedding"),
    ],
)
def test_publication_index_build_rejects_invalid_embedding_provider_output(
    provider: _FakeEmbeddingProvider,
) -> None:
    publication = _publication((_record("R-PUBLISHED"),))

    with pytest.raises(ValueError, match="Embedding provider"):
        build_knowledge_index(
            publication,
            retrieval_version=load_default_retrieval_config().version,
            embedding_provider=provider,
        )


class _ExplodingCompatibility:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError(f"formal Retrieval read compatibility field {name}")


def test_formal_query_planner_never_reads_compatibility_retrieval_query() -> None:
    analysis, context, _ = _analysis_and_context()
    analysis.retrieval_query = cast("RetrievalQuery", _ExplodingCompatibility())

    request = build_retrieval_request(
        analysis,
        context,
        target_platform=TargetPlatform(api_level=12),
        resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
        knowledge_token_budget=20,
    )

    assert len(request.units) == len(analysis.review_units)
    assert all(item.routing_tags == ("has_timer",) for item in request.units)
    assert all(item.exact_tags == () for item in request.units)


def test_query_planner_builds_auditable_changed_code_semantic_context() -> None:
    analysis, context, _ = _analysis_and_context()

    request = build_retrieval_request(
        analysis,
        context,
        target_platform=TargetPlatform(api_level=12),
        resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
        knowledge_token_budget=20,
    )

    for unit in request.units:
        assert "kind: fallback" in unit.intent_summary
        assert "symbol: hunk-L2-L2" in unit.intent_summary
        assert "改动是否正确且不会引入直接回归" in unit.intent_summary
        assert unit.semantic_code_excerpt is not None
        assert "Interval(" in unit.semantic_code_excerpt
        semantic_query = query_embedding_text(unit)
        assert unit.semantic_code_excerpt in semantic_query
        assert "改动是否正确且不会引入直接回归" not in semantic_query


def test_query_planner_rejects_context_plan_from_another_change_set() -> None:
    analysis, _, _ = _analysis_and_context(path="src/A.ets")
    _, other_context, _ = _analysis_and_context(path="src/B.ets")

    with pytest.raises(ValueError, match="different ChangeSets"):
        build_retrieval_request(
            analysis,
            other_context,
            target_platform=TargetPlatform(),
            resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
            knowledge_token_budget=20,
        )


def test_query_planner_rejects_context_bindings_that_disagree_with_routing() -> None:
    analysis, context, snapshots = _analysis_and_context()
    mismatched = ContextPlanner().plan(
        change_set_id=context.change_set_id,
        primary_units=tuple(analysis.review_units),
        primary_question_bindings=tuple(
            QuestionBinding(item.unit_id, "RQ-security")
            for item in analysis.review_units
        ),
        source_snapshots=snapshots,
        candidates=(),
        relation_edges=(),
        code_context_budget=1000,
    )

    with pytest.raises(ValueError, match="question bindings do not match"):
        build_retrieval_request(
            analysis,
            mismatched,
            target_platform=TargetPlatform(),
            resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
            knowledge_token_budget=20,
        )


def test_query_planner_uses_independent_budget_and_normalizes_explicit_rules() -> None:
    analysis, context, _ = _analysis_and_context(code_context_budget=1000)
    first_unit_id = min(item.unit_id for item in analysis.review_units)

    request = build_retrieval_request(
        analysis,
        context,
        target_platform=TargetPlatform(),
        resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
        knowledge_token_budget=21,
        requested_rule_ids_by_unit={first_unit_id: ("R-B", "R-A", "R-B")},
    )

    assert request.total_knowledge_token_budget == 21
    assert sum(item.knowledge_token_budget for item in request.units) == 21
    assert [item.knowledge_token_budget for item in request.units] == [11, 10]
    requested = {item.unit_id: item.requested_rule_ids for item in request.units}
    assert requested[first_unit_id] == ("R-A", "R-B")
    assert context.budget_summary.limit == 1000


def test_query_planner_rejects_unknown_rule_unit_and_non_tuple_values() -> None:
    analysis, context, _ = _analysis_and_context()

    with pytest.raises(ValueError, match="unknown Units"):
        build_retrieval_request(
            analysis,
            context,
            target_platform=TargetPlatform(),
            resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
            knowledge_token_budget=20,
            requested_rule_ids_by_unit={"missing-unit": ("R-A",)},
        )
    unit_id = analysis.review_units[0].unit_id
    malformed = cast("dict[str, tuple[str, ...]]", {unit_id: ["R-A"]})
    with pytest.raises(ValueError, match="must be tuples"):
        build_retrieval_request(
            analysis,
            context,
            target_platform=TargetPlatform(),
            resolved_index_version=f"knowledge-index:sha256:{'7' * 64}",
            knowledge_token_budget=20,
            requested_rule_ids_by_unit=malformed,
        )


def test_cache_fingerprint_is_content_stable_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    first = cache / "model.bin"
    first.write_bytes(b"model-v1")

    fingerprint = _cache_fingerprint(cache)
    assert fingerprint == _cache_fingerprint(cache)
    first.write_bytes(b"model-v2")
    assert _cache_fingerprint(cache) != fingerprint

    (cache / "linked.bin").symlink_to(first)
    with pytest.raises(ValueError, match="must not contain symlinks"):
        _cache_fingerprint(cache)


@pytest.mark.parametrize(
    ("model_id", "dimensions"),
    [("", 2), (" fixture", 2), ("fixture", 0), ("fixture", True)],
)
def test_fastembed_rejects_invalid_metadata_before_optional_import(
    tmp_path: Path,
    model_id: str,
    dimensions: int,
) -> None:
    with pytest.raises(ValueError, match="metadata is invalid"):
        FastEmbedProvider(
            model_id=model_id,
            dimensions=dimensions,
            cache_dir=tmp_path / "cache",
        )
