from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import test_retrieval_shadow_v3 as shadow_helpers
from test_retrieval_core import _index, _record

from arkts_code_reviewer.knowledge.models import Applicability, SourceRef
from arkts_code_reviewer.retrieval.models import KnowledgeIndex
from arkts_code_reviewer.retrieval.query_planner_v3 import VerifiedRetrievalRequestV3
from arkts_code_reviewer.retrieval.request_v3 import RetrievalRequestV3
from arkts_code_reviewer.retrieval.shadow_models_v3 import RetrievalShadowResultV3
from arkts_code_reviewer.retrieval.shadow_service_v3 import RetrievalShadowServiceV3
from arkts_code_reviewer.retrieval_validation.document_truth import (
    RetrievalDocumentTruthV1,
    load_retrieval_document_truth_v1,
    seal_retrieval_document_truth_v1,
)
from arkts_code_reviewer.retrieval_validation.shadow_evaluation import (
    RetrievalShadowEvaluationInputV1,
    build_retrieval_shadow_evaluation_v1,
    load_retrieval_shadow_evaluation_v1,
    verify_retrieval_shadow_evaluation_v1,
)


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    positive_tag: str | None = "has_network",
    total_budget: int = 128,
    embedded: bool = False,
) -> tuple[KnowledgeIndex, VerifiedRetrievalRequestV3, RetrievalShadowResultV3]:
    index = _index(
        (
            _record(
                "R-AI",
                tags=("has_network",),
                dimension_ids=("DIM-11",),
                embedding=(1.0, 0.0) if embedded else None,
            ),
            _record(
                "R-STATIC",
                tags=("has_async",),
                dimension_ids=("DIM-07",),
                embedding=(0.0, 1.0) if embedded else None,
            ),
        ),
        embedded=embedded,
    )
    authority = shadow_helpers._verified_request(  # noqa: SLF001
        monkeypatch,
        index,
        positive_tag=positive_tag,
        total_budget=total_budget,
    )
    result = RetrievalShadowServiceV3(
        index,
        allow_golden_fixture=True,
    ).compare(authority)
    return index, authority, result.artifact


def _truth_clause(
    index: KnowledgeIndex,
    rule_id: str,
    relevance: str,
) -> dict[str, object]:
    record = next(item for item in index.records if item.clause.rule_id == rule_id)
    return {
        "rule_id": rule_id,
        "relevance": relevance,
        "source_ref": record.clause.source_ref,
        "heading_path": record.clause.heading_path,
        "rule_type": record.clause.rule_type,
        "applicability": record.clause.applicability,
    }


def _missing_truth_clause(index: KnowledgeIndex) -> dict[str, object]:
    source = index.records[0].clause.source_ref.model_copy(
        update={"anchor": "R-MISSING"},
    )
    return {
        "rule_id": "R-MISSING",
        "relevance": "required",
        "source_ref": source,
        "heading_path": ("Missing required guidance",),
        "rule_type": "test-missing-type",
        "applicability": index.records[0].clause.applicability,
    }


def _truth_payload(
    index: KnowledgeIndex,
    authority: VerifiedRetrievalRequestV3,
    *,
    include_ai: bool = True,
    include_static: bool = True,
    include_missing: bool = True,
) -> dict[str, object]:
    clauses: list[dict[str, object]] = []
    if include_ai:
        clauses.append(_truth_clause(index, "R-AI", "required"))
    if include_missing:
        clauses.append(_missing_truth_clause(index))
    if include_static:
        clauses.append(_truth_clause(index, "R-STATIC", "acceptable"))
    clauses.sort(key=lambda item: str(item["rule_id"]))
    return {
        "schema_version": "retrieval-document-truth-v1",
        "index_version": index.index_version,
        "feature_config_version": index.feature_config_version,
        "knowledge_build_id": index.published_build_id,
        "source_bundle_id": index.source_bundle_id,
        "target_platform": authority.request.target_platform,
        "units": tuple(
            {
                "unit_id": unit.unit_id,
                "source_ref_id": unit.source_ref_id,
                "profile_id": unit.profile_id,
                "split": "development" if ordinal == 0 else "calibration",
                "critical_dimension_ids": ("DIM-11",),
                "clauses": tuple(dict(clause) for clause in clauses),
            }
            for ordinal, unit in enumerate(authority.request.units)
        ),
        "truth_scope": "development_calibration_only",
        "evidence_qualification_status": "not_qualified",
        "production_qualified": False,
    }


def _truth(
    index: KnowledgeIndex,
    authority: VerifiedRetrievalRequestV3,
    **options: bool,
) -> RetrievalDocumentTruthV1:
    return seal_retrieval_document_truth_v1(_truth_payload(index, authority, **options))


def _input(
    truth: RetrievalDocumentTruthV1,
    index: KnowledgeIndex,
    authority: VerifiedRetrievalRequestV3,
    result: RetrievalShadowResultV3,
) -> RetrievalShadowEvaluationInputV1:
    return RetrievalShadowEvaluationInputV1(
        truth=truth,
        request=authority.request,
        result=result,
        index=index,
    )


def test_d0_report_separates_retrieval_gain_from_knowledge_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    truth = _truth(index, authority)
    evaluation_input = _input(truth, index, authority, result)

    report = build_retrieval_shadow_evaluation_v1(evaluation_input)

    assert report.evaluation_scope == "relative_gain_on_fixed_index"
    assert report.authority_status == "serialized_audit_only"
    assert report.evidence_qualification_status == "not_qualified"
    assert report.production_qualified is False
    assert report.user_visible is False
    assert report.prompt_eligible is False
    assert report.finding_evidence_eligible is False
    assert "document_truth_split_leakage_not_sealed" in report.qualification_blockers
    assert report.unit_count == 2
    assert report.development_unit_count == 1
    assert report.calibration_unit_count == 1
    assert tuple(item.split for item in report.split_aggregates) == (
        "development",
        "calibration",
    )
    assert all(item.unit_count == 1 for item in report.split_aggregates)
    assert report.knowledge_gap.value == 0.5

    for unit in report.unit_evaluations:
        assert unit.index_eligible_required_rule_ids == ("R-AI",)
        assert unit.missing_required_rule_ids == ("R-MISSING",)
        assert unit.knowledge_gap.value == 0.5
        static, hybrid = unit.arms
        assert static.pre_budget.ordered_rule_ids == ("R-STATIC",)
        assert hybrid.pre_budget.ordered_rule_ids == ("R-STATIC", "R-AI")
        assert static.pre_budget.metrics_at_k[1].retriever_required_recall.value == 0.0
        assert hybrid.pre_budget.metrics_at_k[1].retriever_required_recall.value == 1.0
        assert static.pre_budget.metrics_at_k[1].full_chain_required_coverage.value == 0.0
        assert hybrid.pre_budget.metrics_at_k[1].full_chain_required_coverage.value == 0.5
        assert static.pre_budget.metrics_at_k[1].precision.value == 1.0
        assert hybrid.pre_budget.metrics_at_k[1].precision.value == 1.0
        assert static.pre_budget.metrics_at_k[1].critical_dimension_coverage.value == 0.0
        assert hybrid.pre_budget.metrics_at_k[1].critical_dimension_coverage.value == 1.0
        assert unit.comparison.pre_budget_added_rule_ids == ("R-AI",)
        assert unit.comparison.pre_budget_removed_rule_ids == ()
        assert unit.comparison.comparison_eligible is True
        assert unit.comparison.pre_budget_retriever_required_mrr_delta == 0.5

    static_aggregate, hybrid_aggregate = report.arm_aggregates
    assert static_aggregate.pre_budget.metrics_at_k[1].retriever_required_recall.value == 0.0
    assert hybrid_aggregate.pre_budget.metrics_at_k[1].retriever_required_recall.value == 1.0
    assert report.arm_comparison.pre_budget_metric_deltas[1].retriever_required_recall_delta == 1.0
    assert report.arm_comparison.pre_budget_added_unit_rule_count == 2
    assert report.arm_comparison.comparable_unit_count == 2

    verify_retrieval_shadow_evaluation_v1(report, evaluation_input)
    assert load_retrieval_document_truth_v1(truth.model_dump_json()) == truth
    assert load_retrieval_shadow_evaluation_v1(report.model_dump_json()) == report


def test_unlabelled_observed_clause_is_not_silently_counted_as_irrelevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    truth = _truth(index, authority, include_static=False)

    with pytest.raises(ValueError, match="unlabelled Clauses"):
        build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))


def test_required_clause_outside_index_is_only_a_knowledge_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    payload = _truth_payload(index, authority)
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        unit["clauses"] = tuple(
            {**clause, "relevance": "acceptable"} if clause["rule_id"] == "R-AI" else clause
            for clause in clauses
        )
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        static, hybrid = unit.arms
        assert unit.index_eligible_required_rule_ids == ()
        assert unit.missing_required_rule_ids == ("R-MISSING",)
        assert static.pre_budget.metrics_at_k[3].retriever_required_recall.value is None
        assert hybrid.pre_budget.metrics_at_k[3].retriever_required_recall.value is None
        assert static.pre_budget.metrics_at_k[3].full_chain_required_coverage.value == 0.0
        assert hybrid.pre_budget.metrics_at_k[3].full_chain_required_coverage.value == 0.0
        assert static.pre_budget.retriever_required_reciprocal_rank is None
        assert static.pre_budget.full_chain_required_reciprocal_rank == 0.0


def test_pre_budget_gain_is_not_confused_with_post_budget_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch, total_budget=16)
    truth = _truth(index, authority)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        static, hybrid = unit.arms
        assert hybrid.pre_budget.ordered_rule_ids == ("R-STATIC", "R-AI")
        assert static.selected.ordered_rule_ids == ("R-STATIC",)
        assert hybrid.selected.ordered_rule_ids == ("R-STATIC",)
        assert unit.comparison.pre_budget_added_rule_ids == ("R-AI",)
        assert unit.comparison.selected_added_rule_ids == ()
        assert unit.comparison.pre_budget_metric_deltas[1].retriever_required_recall_delta == 1.0
        assert unit.comparison.selected_metric_deltas[1].retriever_required_recall_delta == 0.0


def test_forbidden_ai_clause_is_reported_as_a_hard_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    payload = _truth_payload(index, authority, include_missing=False)
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        unit["critical_dimension_ids"] = ()
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        unit["clauses"] = tuple(
            {**clause, "relevance": "forbidden"} if clause["rule_id"] == "R-AI" else clause
            for clause in clauses
        )
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        static, hybrid = unit.arms
        assert static.selected.forbidden_hit_rule_ids == ()
        assert hybrid.selected.forbidden_hit_rule_ids == ("R-AI",)
        assert unit.comparison.observed_pre_budget_forbidden_hit_delta == 1
        assert unit.comparison.observed_selected_forbidden_hit_delta == 1
    assert report.arm_comparison.observed_pre_budget_forbidden_hit_delta == 2
    assert report.arm_comparison.observed_selected_forbidden_hit_delta == 2


def test_pre_budget_forbidden_observation_is_separate_from_selected_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch, total_budget=16)
    payload = _truth_payload(index, authority, include_missing=False)
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        unit["critical_dimension_ids"] = ()
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        unit["clauses"] = tuple(
            {**clause, "relevance": "forbidden"} if clause["rule_id"] == "R-AI" else clause
            for clause in clauses
        )
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        assert unit.comparison.observed_pre_budget_forbidden_hit_delta == 1
        assert unit.comparison.observed_selected_forbidden_hit_delta == 0
    assert report.arm_comparison.observed_pre_budget_forbidden_hit_delta == 2
    assert report.arm_comparison.observed_selected_forbidden_hit_delta == 0


def test_development_and_calibration_aggregates_are_not_mixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    payload = _truth_payload(index, authority, include_missing=False)
    units = payload["units"]
    assert isinstance(units, tuple)
    calibration = next(
        unit for unit in units if isinstance(unit, dict) and unit["split"] == "calibration"
    )
    calibration["critical_dimension_ids"] = ()
    clauses = calibration["clauses"]
    assert isinstance(clauses, tuple)
    calibration["clauses"] = tuple(
        {**clause, "relevance": "forbidden"} if clause["rule_id"] == "R-AI" else clause
        for clause in clauses
    )
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    development, calibration_aggregate = report.split_aggregates
    assert development.split == "development"
    assert (
        development.arm_comparison.pre_budget_metric_deltas[1].retriever_required_recall_delta
        == 1.0
    )
    assert development.arm_comparison.observed_pre_budget_forbidden_hit_delta == 0
    assert calibration_aggregate.split == "calibration"
    assert (
        calibration_aggregate.arm_comparison.pre_budget_metric_deltas[
            1
        ].retriever_required_recall_delta
        is None
    )
    assert calibration_aggregate.arm_comparison.observed_pre_budget_forbidden_hit_delta == 1


def test_empty_split_is_not_forged_into_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    payload = _truth_payload(index, authority)
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        unit["split"] = "development"
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    assert report.development_unit_count == 2
    assert report.calibration_unit_count == 0
    assert tuple(item.split for item in report.split_aggregates) == ("development",)


def test_degraded_runtime_keeps_observed_metrics_but_excludes_quality_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch, embedded=True)
    truth = _truth(index, authority)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        static, hybrid = unit.arms
        assert static.degraded is True
        assert hybrid.degraded is True
        assert "embedding_unavailable" in static.diagnostic_codes
        assert hybrid.pre_budget.metrics_at_k[1].retriever_required_recall.value == 1.0
        assert unit.comparison.comparison_eligible is False
        assert unit.comparison.exclusion_reasons == ("runtime_degraded",)
        assert all(
            value is None
            for delta in (
                *unit.comparison.pre_budget_metric_deltas,
                *unit.comparison.selected_metric_deltas,
            )
            for value in (
                delta.retriever_required_recall_delta,
                delta.full_chain_required_coverage_delta,
                delta.precision_delta,
                delta.critical_dimension_coverage_delta,
            )
        )
        assert unit.comparison.pre_budget_retriever_required_mrr_delta is None
        assert unit.comparison.selected_retriever_required_mrr_delta is None
    assert report.arm_comparison.comparable_unit_count == 0
    assert report.arm_comparison.excluded_unit_count == 2
    assert all(
        value is None
        for delta in (
            *report.arm_comparison.pre_budget_metric_deltas,
            *report.arm_comparison.selected_metric_deltas,
        )
        for value in (
            delta.retriever_required_recall_delta,
            delta.full_chain_required_coverage_delta,
            delta.precision_delta,
            delta.critical_dimension_coverage_delta,
        )
    )
    assert report.arm_comparison.pre_budget_retriever_required_mrr_delta is None
    assert report.arm_comparison.selected_retriever_required_mrr_delta is None


def test_degraded_runtime_keeps_all_observed_safety_deltas_but_not_added_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch, embedded=True)
    excluded = Applicability(min_api_level=999)
    rebound_records = tuple(
        record.model_copy(
            update={
                "clause": record.clause.model_copy(update={"applicability": excluded})
            }
        )
        if record.clause.rule_id == "R-AI"
        else record
        for record in index.records
    )
    rebound_index = KnowledgeIndex.create(
        origin=index.origin,
        published_build_id=index.published_build_id,
        source_bundle_id=index.source_bundle_id,
        feature_config_version=index.feature_config_version,
        annotation_version=index.annotation_version,
        catalog_version=index.catalog_version,
        retrieval_version=index.retrieval_version,
        retrieval_config_fingerprint=index.retrieval_config_fingerprint,
        embedding_model=index.embedding_model,
        embedding_version=index.embedding_version,
        embedding_dimensions=index.embedding_dimensions,
        api_symbols=index.api_symbols,
        records=rebound_records,
    )
    rebound_request = RetrievalRequestV3.create(
        context_plan_id=authority.request.context_plan_id,
        feature_routing_id=authority.request.feature_routing_id,
        feature_config_version=authority.request.feature_config_version,
        index_version=rebound_index.index_version,
        target_platform=authority.request.target_platform,
        total_knowledge_token_budget=authority.request.total_knowledge_token_budget,
        units=authority.request.units,
    )
    rebound_result = RetrievalShadowResultV3.create(
        verified_request_id=rebound_request.request_id,
        v1_control_request_id=result.v1_control_request_id,
        v1_control_evidence_pack_id=result.v1_control_evidence_pack_id,
        base_retrieval_config_fingerprint=result.base_retrieval_config_fingerprint,
        shadow_policy_fingerprint=result.shadow_policy_fingerprint,
        index_version=rebound_index.index_version,
        index_origin=result.index_origin,
        knowledge_build_id=result.knowledge_build_id,
        source_bundle_id=result.source_bundle_id,
        embedding_version=result.embedding_version,
        formal_attestation_ids=result.formal_attestation_ids,
        units=result.units,
    )
    payload = _truth_payload(index, authority, include_missing=False)
    payload["index_version"] = rebound_index.index_version
    units = payload["units"]
    assert isinstance(units, tuple)
    for unit in units:
        assert isinstance(unit, dict)
        unit["critical_dimension_ids"] = ()
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        unit["clauses"] = tuple(
            {
                **clause,
                "relevance": "forbidden",
                "applicability": excluded,
            }
            if clause["rule_id"] == "R-AI"
            else clause
            for clause in clauses
        )
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(
        RetrievalShadowEvaluationInputV1(
            truth=truth,
            request=rebound_request,
            result=rebound_result,
            index=rebound_index,
        )
    )

    for unit in report.unit_evaluations:
        assert unit.comparison.comparison_eligible is False
        assert unit.comparison.observed_pre_budget_forbidden_hit_delta == 1
        assert unit.comparison.observed_selected_forbidden_hit_delta == 1
        assert unit.comparison.observed_pre_budget_applicability_violation_delta == 1
        assert unit.comparison.observed_selected_applicability_violation_delta == 1
    assert report.arm_comparison.comparable_unit_count == 0
    assert report.arm_comparison.excluded_unit_count == 2
    assert report.arm_comparison.pre_budget_added_unit_rule_count == 0
    assert report.arm_comparison.selected_added_unit_rule_count == 0
    assert report.arm_comparison.observed_pre_budget_forbidden_hit_delta == 2
    assert report.arm_comparison.observed_selected_forbidden_hit_delta == 2
    assert report.arm_comparison.observed_pre_budget_applicability_violation_delta == 2
    assert report.arm_comparison.observed_selected_applicability_violation_delta == 2


def test_true_negative_empty_does_not_forge_perfect_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((_record("R-UNUSED", tags=("has_logging",)),))
    authority = shadow_helpers._verified_request(  # noqa: SLF001
        monkeypatch,
        index,
        positive_tag=None,
        total_budget=128,
    )
    result = (
        RetrievalShadowServiceV3(
            index,
            allow_golden_fixture=True,
        )
        .compare(authority)
        .artifact
    )
    payload = _truth_payload(
        index,
        authority,
        include_ai=False,
        include_static=False,
        include_missing=False,
    )
    for unit in payload["units"]:  # type: ignore[union-attr]
        unit["critical_dimension_ids"] = ()
    truth = seal_retrieval_document_truth_v1(payload)

    report = build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))

    for unit in report.unit_evaluations:
        static, hybrid = unit.arms
        assert static.truth_case_kind == "true_negative"
        assert static.selected_empty is True
        assert hybrid.selected_empty is True
        assert static.selected.metrics_at_k[0].precision.value is None
        assert unit.comparison.comparison_eligible is False
        assert unit.comparison.exclusion_reasons == ("formal_execution_not_valid",)
    assert report.arm_aggregates[0].selected.metrics_at_k[0].precision.value is None


def test_truth_loader_rejects_stale_hash_duplicate_keys_and_wrong_root() -> None:
    raw = '{"truth_id":"retrieval-document-truth:sha256:' + "0" * 64 + '","truth_id":null}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_retrieval_document_truth_v1(raw)
    with pytest.raises(ValueError, match="top-level value"):
        load_retrieval_document_truth_v1("[]")
    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_retrieval_document_truth_v1('{"value":NaN}')
    with pytest.raises(ValueError, match="UTF-8"):
        load_retrieval_document_truth_v1(b"\xff")


def test_truth_self_hash_labels_and_target_applicability_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, _result = _runtime(monkeypatch)
    truth = _truth(index, authority)

    stale = truth.model_dump(mode="json")
    stale["units"][0]["clauses"][0]["relevance"] = "forbidden"
    with pytest.raises(ValueError, match="Truth ID does not match"):
        load_retrieval_document_truth_v1(json.dumps(stale))

    duplicate = _truth_payload(index, authority)
    units = duplicate["units"]
    assert isinstance(units, tuple)
    first = units[0]
    assert isinstance(first, dict)
    clauses = first["clauses"]
    assert isinstance(clauses, tuple)
    first["clauses"] = (clauses[0], clauses[0], *clauses[1:])
    with pytest.raises(ValueError, match="rule-sorted and unique"):
        seal_retrieval_document_truth_v1(duplicate)

    excluded = _truth_payload(index, authority)
    excluded_units = excluded["units"]
    assert isinstance(excluded_units, tuple)
    for unit in excluded_units:
        assert isinstance(unit, dict)
        clauses = unit["clauses"]
        assert isinstance(clauses, tuple)
        unit["clauses"] = tuple(
            {
                **clause,
                "applicability": {
                    "min_api_level": 999,
                    "max_api_level": None,
                    "releases": (),
                    "language_modes": (),
                    "permissions": (),
                    "system_capabilities": (),
                },
            }
            if clause["rule_id"] == "R-AI"
            else clause
            for clause in clauses
        )
    with pytest.raises(ValueError, match="cannot be excluded"):
        seal_retrieval_document_truth_v1(excluded)


def test_report_self_hash_and_full_rebuild_reject_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    truth = _truth(index, authority)
    evaluation_input = _input(truth, index, authority, result)
    report = build_retrieval_shadow_evaluation_v1(evaluation_input)

    stale = report.model_dump(mode="json")
    stale["truth_id"] = "retrieval-document-truth:sha256:" + "f" * 64
    with pytest.raises(ValueError, match="ID does not match"):
        load_retrieval_shadow_evaluation_v1(json.dumps(stale))

    rebound: dict[str, Any] = report.model_dump(mode="json")
    rebound["truth_id"] = "retrieval-document-truth:sha256:" + "f" * 64
    rebound.pop("report_id")
    rebound["report_id"] = _canonical_hash("retrieval-shadow-evaluation", rebound)
    forged = load_retrieval_shadow_evaluation_v1(json.dumps(rebound))
    with pytest.raises(ValueError, match="does not rebuild"):
        verify_retrieval_shadow_evaluation_v1(forged, evaluation_input)

    metric_forgery: dict[str, Any] = report.model_dump(mode="json")
    metric = metric_forgery["unit_evaluations"][0]["arms"][1]["pre_budget"]["metrics_at_k"][1][
        "retriever_required_recall"
    ]
    metric["numerator"] = 0
    metric["value"] = 0.0
    metric_forgery.pop("report_id")
    metric_forgery["report_id"] = _canonical_hash(
        "retrieval-shadow-evaluation",
        metric_forgery,
    )
    with pytest.raises(ValueError, match="Unit comparison does not rebuild"):
        load_retrieval_shadow_evaluation_v1(json.dumps(metric_forgery))

    split_forgery: dict[str, Any] = report.model_dump(mode="json")
    split_forgery["split_aggregates"][0]["knowledge_gap"] = {
        "numerator": 0,
        "denominator": 2,
        "value": 0.0,
    }
    split_forgery.pop("report_id")
    split_forgery["report_id"] = _canonical_hash(
        "retrieval-shadow-evaluation",
        split_forgery,
    )
    with pytest.raises(ValueError, match="split aggregates do not rebuild"):
        load_retrieval_shadow_evaluation_v1(json.dumps(split_forgery))


def test_truth_clause_metadata_must_match_the_frozen_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, authority, result = _runtime(monkeypatch)
    payload = _truth_payload(index, authority)
    units = payload["units"]
    assert isinstance(units, tuple)
    first_unit = units[0]
    assert isinstance(first_unit, dict)
    clauses = first_unit["clauses"]
    assert isinstance(clauses, tuple)
    first_clause = dict(clauses[0])
    source_ref = first_clause["source_ref"]
    assert isinstance(source_ref, SourceRef)
    first_clause["source_ref"] = source_ref.model_copy(update={"anchor": "forged-anchor"})
    first_unit["clauses"] = (first_clause, *clauses[1:])
    truth = seal_retrieval_document_truth_v1(payload)

    with pytest.raises(ValueError, match="Truth Clause differs"):
        build_retrieval_shadow_evaluation_v1(_input(truth, index, authority, result))
