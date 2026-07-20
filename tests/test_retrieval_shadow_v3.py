from __future__ import annotations

import json
import pickle
from dataclasses import replace

import pytest
import test_retrieval_request_v3 as request_v3_helpers
from test_retrieval_core import (
    _FakeEmbeddingProvider,
    _index,
    _record,
)
from test_retrieval_request_v2 import _EGRESS_POLICY, _TARGET

from arkts_code_reviewer.code_analysis.context_planning import ContextPlanner
from arkts_code_reviewer.knowledge.models import Applicability
from arkts_code_reviewer.retrieval.config import (
    RetrievalConfig,
    load_default_retrieval_config,
)
from arkts_code_reviewer.retrieval.models import KnowledgeIndex, load_evidence_pack
from arkts_code_reviewer.retrieval.query_planner_v3 import (
    TrustedRetrievalRequestV3Builder,
    VerifiedRetrievalRequestV3,
)
from arkts_code_reviewer.retrieval.request_v3 import (
    load_retrieval_request_v3,
    render_vector_query_v3,
)
from arkts_code_reviewer.retrieval.service import RetrievalService
from arkts_code_reviewer.retrieval.shadow_models_v3 import (
    ShadowArmResultV3,
    ShadowCandidatePoolV3,
    ShadowPoolId,
    ShadowUnitComparisonV3,
    load_retrieval_shadow_result_v3,
)
from arkts_code_reviewer.retrieval.shadow_policy_v3 import (
    RetrievalShadowPolicyV3,
    load_default_retrieval_shadow_policy_v3,
)
from arkts_code_reviewer.retrieval.shadow_service_v3 import (
    RetrievalShadowServiceV3,
)


class _RecordingEmbeddingProvider(_FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(query_vector=(1.0, 0.0))
        self.query_texts: list[str] = []

    def embed_query(self, text: str) -> tuple[float, ...]:
        self.query_texts.append(text)
        return super().embed_query(text)


def _verified_request(
    monkeypatch: pytest.MonkeyPatch,
    index: KnowledgeIndex,
    *,
    positive_tag: str | None,
    total_budget: int = 128,
    context_blocked: bool = False,
) -> VerifiedRetrievalRequestV3:
    if context_blocked:
        original = request_v3_helpers._scenario()  # noqa: SLF001
        analysis = original.analysis
        assert analysis.change_set is not None
        blocked_context = ContextPlanner().plan(
            change_set_id=analysis.change_set.change_set_id,
            primary_units=tuple(analysis.review_units),
            primary_question_bindings=original.context_plan.primary_question_bindings,
            source_snapshots=original.snapshots,
            candidates=(),
            relation_edges=(),
            blocking_change_ids=tuple(atom.atom_id for atom in analysis.change_set.atoms),
            code_context_budget=original.context_plan.budget_summary.limit,
        )
        blocked = replace(original, context_plan=blocked_context)
        monkeypatch.setattr(request_v3_helpers, "_scenario", lambda: blocked)

    scenario, evidence, verifier = request_v3_helpers._formal_evidence(  # noqa: SLF001
        monkeypatch,
        positive_tag=positive_tag,
    )
    return TrustedRetrievalRequestV3Builder(
        formal_execution_verifier=verifier,
        analysis_context_policy=_EGRESS_POLICY,
    ).build(
        scenario.analysis,
        scenario.context_plan,
        source_snapshots=scenario.snapshots,
        formal_evidence_by_unit=evidence,
        target_platform=_TARGET,
        resolved_index_version=index.index_version,
        knowledge_token_budget=total_budget,
    )


def _pool(
    unit: ShadowUnitComparisonV3,
    pool_id: ShadowPoolId,
) -> ShadowCandidatePoolV3:
    return next(pool for pool in unit.pools if pool.pool == pool_id)


def _without_arm_label(arm: ShadowArmResultV3) -> dict[str, object]:
    payload = arm.model_dump(mode="json")
    payload.pop("arm")
    return payload


def _index_for_config(index: KnowledgeIndex, config: RetrievalConfig) -> KnowledgeIndex:
    return KnowledgeIndex.create(
        origin=index.origin,
        published_build_id=index.published_build_id,
        source_bundle_id=index.source_bundle_id,
        feature_config_version=index.feature_config_version,
        annotation_version=index.annotation_version,
        catalog_version=index.catalog_version,
        retrieval_version=config.version,
        retrieval_config_fingerprint=config.fingerprint,
        embedding_model=index.embedding_model,
        embedding_version=index.embedding_version,
        embedding_dimensions=index.embedding_dimensions,
        api_symbols=index.api_symbols,
        records=index.records,
    )


def _policy_for_config(config: RetrievalConfig) -> RetrievalShadowPolicyV3:
    payload = load_default_retrieval_shadow_policy_v3().model_dump(mode="json")
    payload["base_retrieval_config_fingerprint"] = config.fingerprint
    payload["rrf_k"] = config.rrf_k
    payload["result_limit"] = config.result_limit
    return RetrievalShadowPolicyV3.model_validate(
        payload,
        context={"base_retrieval_config": config},
    )


def test_compare_rejects_raw_v3_v1_and_json_before_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-VECTOR", tags=("has_network",), embedding=(1.0, 0.0)),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )
    provider = _FakeEmbeddingProvider()
    service = RetrievalShadowServiceV3(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    )
    raw_v3 = authority.request
    loaded_v3 = load_retrieval_request_v3(raw_v3.model_dump_json())
    invalid_inputs = (
        raw_v3,
        loaded_v3,
        authority.baseline_request,
        raw_v3.model_dump_json(),
    )

    for invalid in invalid_inputs:
        with pytest.raises(
            TypeError,
            match="exact VerifiedRetrievalRequestV3 authority",
        ):
            service.compare(invalid)  # type: ignore[arg-type]

    assert provider.query_calls == 0


def test_ai_only_candidate_uses_ai_inferred_scope_and_never_unit_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-AI", tags=("has_network",), token_count=11),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert unit.exact_tags == ("has_async",)
        assert unit.ai_inferred_tags == ("has_network",)
        assert unit.static_vector.selected_rule_ids == ()
        assert unit.hybrid.selected_rule_ids == ("R-AI",)
        ai_pool = _pool(unit, "ai_inferred")
        assert [candidate.rule_id for candidate in ai_pool.candidates] == ["R-AI"]
        clause = unit.hybrid.ranked_clauses[0]
        assert {(match.kind, match.scope, match.value) for match in clause.matched_by} == {
            ("tag", "ai_inferred", "has_network")
        }
        assert all(match.scope != "unit_exact" for match in clause.matched_by)
        assert [item.pool for item in clause.contributions] == ["ai_inferred"]


def test_static_ai_agreement_deduplicates_clause_and_preserves_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-BOTH", tags=("has_async",), token_count=13),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_async",
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert unit.hybrid.selected_rule_ids == ("R-BOTH",)
        assert unit.hybrid.used_tokens == 13
        assert len(unit.hybrid.ranked_clauses) == 1
        clause = unit.hybrid.ranked_clauses[0]
        assert {(match.scope, match.value) for match in clause.matched_by} == {
            ("unit_exact", "has_async"),
            ("ai_inferred", "has_async"),
        }
        assert [item.pool for item in clause.contributions] == [
            "formal_exact",
            "ai_inferred",
        ]
        assert [item.rule_id for item in _pool(unit, "formal_exact").candidates] == ["R-BOTH"]
        assert [item.rule_id for item in _pool(unit, "ai_inferred").candidates] == ["R-BOTH"]


def test_keyword_match_uses_text_keyword_scope_while_v1_control_stays_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-KEYWORD", raw_keywords=("console.info",)),))
    authority = _verified_request(monkeypatch, index, positive_tag=None)

    result = RetrievalShadowServiceV3(
        index,
        allow_golden_fixture=True,
    ).compare(authority)

    for unit, control in zip(
        result.artifact.units,
        result.control_evidence_pack.units,
        strict=True,
    ):
        candidate = _pool(unit, "text_keyword").candidates[0]
        assert candidate.rule_id == "R-KEYWORD"
        assert {(item.kind, item.scope, item.value) for item in candidate.matched_by} == {
            ("keyword", "text_keyword", "console.info")
        }
        assert unit.static_vector.ranked_clauses[0].matched_by == candidate.matched_by
        assert unit.hybrid.ranked_clauses[0].matched_by == candidate.matched_by
        assert control.clauses[0].matched_by[0].kind == "keyword"
        assert control.clauses[0].matched_by[0].scope == "unit_exact"


def test_unavailable_has_static_hybrid_parity_with_code_first_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-STATIC", tags=("has_async",), embedding=(1.0, 0.0)),))
    authority = _verified_request(monkeypatch, index, positive_tag=None)

    artifact = (
        RetrievalShadowServiceV3(
            index,
            embedding_provider=_FakeEmbeddingProvider(),
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert unit.formal_execution_status == "unavailable"
        assert unit.formal_ai_result_id is None
        assert unit.ai_inferred_tags == ()
        assert unit.tag_disagreements == ()
        assert unit.candidate_dimension_ids == ()
        assert _pool(unit, "ai_inferred").candidates == ()
        assert _without_arm_label(unit.static_vector) == _without_arm_label(unit.hybrid)


def test_code_first_vector_query_excludes_ai_dimensions_rq_attestation_and_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-VECTOR", tags=("has_network",), embedding=(1.0, 0.0)),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )
    provider = _RecordingEmbeddingProvider()

    RetrievalShadowServiceV3(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    ).compare(authority)

    expected = tuple(render_vector_query_v3(unit) for unit in authority.request.units)
    assert all(text is not None for text in expected)
    shadow_queries = tuple(provider.query_texts[-len(expected) :])
    assert shadow_queries == expected
    for text, unit in zip(shadow_queries, authority.request.units, strict=True):
        forbidden = (
            *unit.exact_tags,
            *unit.routing_tags,
            *unit.ai_inferred_tags,
            *unit.retrieval_dimension_ids,
            *unit.routing_dimension_ids,
            *unit.candidate_dimension_ids,
            *unit.review_question_ids,
            unit.trusted_runner_attestation_id,
            unit.intent_summary,
        )
        assert all(value not in text for value in forbidden if value is not None)


def test_runtime_binding_rechecks_do_not_repeat_embedding_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-VECTOR", tags=("has_network",), embedding=(1.0, 0.0)),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )
    provider = _FakeEmbeddingProvider()
    result = RetrievalShadowServiceV3(
        index,
        embedding_provider=provider,
        allow_golden_fixture=True,
    ).compare(authority)
    calls_after_compare = provider.query_calls
    assert calls_after_compare > 0

    _ = result.authority_status
    _ = result.artifact
    _ = result.control_evidence_pack
    _ = result.artifact

    assert provider.query_calls == calls_after_compare


def test_candidate_dimension_does_not_cover_or_reserve_a_formal_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_payload = load_default_retrieval_config().model_dump(mode="python")
    base_payload["result_limit"] = 1
    config = RetrievalConfig.model_validate(base_payload)
    source_index = _index(
        (
            _record(
                "R-CANDIDATE",
                tags=("has_network",),
                dimension_ids=("DIM-11",),
                raw_keywords=("console.info",),
                embedding=(1.0, 0.0),
            ),
            _record(
                "R-FORMAL",
                tags=("has_async",),
                dimension_ids=("DIM-07",),
                embedding=(0.0, 1.0),
            ),
        )
    )
    index = _index_for_config(source_index, config)
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            base_config=config,
            shadow_policy=_policy_for_config(config),
            embedding_provider=_FakeEmbeddingProvider(),
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert unit.formal_dimension_ids == ("DIM-07",)
        assert unit.candidate_dimension_ids == ("DIM-11",)
        assert unit.candidate_dimension_policy == "diagnostic_only"
        for arm in (unit.static_vector, unit.hybrid):
            assert arm.ranked_clauses[0].rule_id == "R-CANDIDATE"
            assert arm.selected_rule_ids == ("R-FORMAL",)
            assert arm.covered_dimension_ids == ("DIM-07",)
            assert arm.uncovered_dimension_ids == ()
            assert "DIM-11" not in arm.covered_dimension_ids


def test_embedding_failure_degrades_both_arms_but_keeps_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        (
            _record(
                "R-STRUCTURED",
                tags=("has_async", "has_network"),
                embedding=(1.0, 0.0),
            ),
        )
    )
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )

    result = RetrievalShadowServiceV3(
        index,
        embedding_provider=_FakeEmbeddingProvider(query_error=OSError("offline")),
        allow_golden_fixture=True,
    ).compare(authority)

    assert result.artifact.degraded is True
    assert result.control_evidence_pack.degraded is True
    for unit in result.artifact.units:
        for arm in (unit.static_vector, unit.hybrid):
            assert arm.selected_rule_ids == ("R-STRUCTURED",)
            assert "embedding_unavailable" in {diagnostic.code for diagnostic in arm.diagnostics}


def test_budget_is_per_unit_and_multisource_clauses_are_charged_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        (
            _record("R-A", tags=("has_async",), token_count=6),
            _record("R-B", tags=("has_async",), token_count=6),
        )
    )
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_async",
        total_budget=20,
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    assert [unit.hybrid.token_budget for unit in artifact.units] == [10, 10]
    for unit in artifact.units:
        assert unit.hybrid.selected_rule_ids == ("R-A",)
        assert unit.hybrid.used_tokens == 6
        assert (
            sum(
                clause.token_count
                for clause in unit.hybrid.ranked_clauses
                if clause.selection_status == "selected"
            )
            == 6
        )
        assert {clause.selection_status for clause in unit.hybrid.ranked_clauses} == {
            "selected",
            "token_budget",
        }
        assert "budget_exhausted" in {diagnostic.code for diagnostic in unit.hybrid.diagnostics}


def test_context_blocked_skips_every_shadow_pool_and_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-BLOCKED", tags=("has_async",)),))
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag=None,
        context_blocked=True,
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert all(pool.candidates == () for pool in unit.pools)
        for arm in (unit.static_vector, unit.hybrid):
            assert arm.ranked_clauses == ()
            assert arm.selected_rule_ids == ()
            assert arm.used_tokens == 0
            assert [diagnostic.code for diagnostic in arm.diagnostics] == [
                "context_dispatch_blocked"
            ]


def test_applicability_exclusion_dominates_ai_inferred_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        (
            _record(
                "R-EXCLUDED",
                tags=("has_network",),
                applicability=Applicability(min_api_level=19),
            ),
        )
    )
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )

    artifact = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )

    for unit in artifact.units:
        assert _pool(unit, "ai_inferred").candidates == ()
        for arm in (unit.static_vector, unit.hybrid):
            assert arm.ranked_clauses == ()
            assert arm.selected_rule_ids == ()
            assert [diagnostic.code for diagnostic in arm.diagnostics] == ["empty_result"]


def test_serialized_artifact_is_audit_only_and_publication_control_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(
        (_record("R-PUBLISHED", tags=("has_async", "has_network")),),
        origin="publication",
    )
    authority = _verified_request(
        monkeypatch,
        index,
        positive_tag="has_network",
    )
    direct_before = RetrievalService(index).retrieve(authority.baseline_request)
    service = RetrievalShadowServiceV3(index)

    first = service.compare(authority)
    second = service.compare(authority)
    direct_after = RetrievalService(index).retrieve(authority.baseline_request)

    assert first.authority_status == "runtime_verified"
    assert first.control_evidence_pack == direct_before == direct_after
    assert first.control_evidence_pack.production_eligible is True
    assert first.artifact == second.artifact
    assert first.artifact.schema_version == "retrieval-shadow-result-v1"
    assert first.artifact.index_origin == "publication"
    assert first.artifact.evidence_qualification_status == "not_qualified"
    assert first.artifact.production_qualified is False
    assert first.artifact.user_visible is False
    assert first.artifact.prompt_eligible is False
    assert first.artifact.finding_evidence_eligible is False
    raw = first.artifact.model_dump_json()
    assert load_retrieval_shadow_result_v3(raw) == first.artifact

    duplicate = raw.replace(
        '"result_id":',
        f'"result_id":"{first.artifact.result_id}","result_id":',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_shadow_result_v3(duplicate)

    payload = first.artifact.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="extra_forbidden"):
        load_retrieval_shadow_result_v3(json.dumps(payload))

    with pytest.raises(ValueError, match="invalid Evidence Pack"):
        load_evidence_pack(raw)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(first)
    with pytest.raises(TypeError, match="exact VerifiedRetrievalRequestV3 authority"):
        service.compare(first.artifact)  # type: ignore[arg-type]
