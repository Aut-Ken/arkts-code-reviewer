from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.retrieval.applicability import evaluate_applicability
from arkts_code_reviewer.retrieval.models import (
    KnowledgeIndex,
    KnowledgeIndexRecord,
    TargetPlatform,
)
from arkts_code_reviewer.retrieval.request_v3 import RetrievalRequestV3, RetrievalUnitRequestV3
from arkts_code_reviewer.retrieval.shadow_models_v3 import (
    FormalExecutionStatus,
    RetrievalShadowResultV3,
    ShadowArmId,
    ShadowArmResultV3,
    ShadowDiagnosticCode,
    ShadowUnitComparisonV3,
)
from arkts_code_reviewer.retrieval_validation.document_truth import (
    DocumentTruthClauseV1,
    DocumentTruthSplit,
    DocumentTruthUnitV1,
    RetrievalDocumentTruthV1,
)

RETRIEVAL_SHADOW_EVALUATION_SCHEMA_VERSION: Literal["retrieval-shadow-evaluation-v1"] = (
    "retrieval-shadow-evaluation-v1"
)

MetricK = Literal[1, 3, 5, 8]
RankingScope = Literal["post_fusion_pre_budget", "post_budget_selected"]
TruthCaseKind = Literal[
    "index_eligible_required",
    "knowledge_gap_required",
    "acceptable_only",
    "true_negative",
]
ComparisonExclusionReason = Literal["formal_execution_not_valid", "runtime_degraded"]

METRIC_K_VALUES: tuple[MetricK, ...] = (1, 3, 5, 8)
DOCUMENT_TRUTH_SPLITS: tuple[DocumentTruthSplit, ...] = ("development", "calibration")
_DEGRADED_CODES: frozenset[ShadowDiagnosticCode] = frozenset(
    {"context_dispatch_blocked", "embedding_unavailable", "parser_degraded"}
)
_QUALIFICATION_BLOCKERS: tuple[str, ...] = (
    "acceptance_holdout_not_run",
    "document_truth_not_consensus_or_git_sealed",
    "document_truth_split_leakage_not_sealed",
    "production_knowledge_quality_not_proven",
    "production_prevalence_not_proven",
    "shadow_policy_root_not_supplied",
    "shadow_result_runtime_authority_not_rebuilt",
)

_HASH = r"[0-9a-f]{64}"
_REPORT_ID_PATTERN = rf"^retrieval-shadow-evaluation:sha256:{_HASH}$"
_TRUTH_ID_PATTERN = rf"^retrieval-document-truth:sha256:{_HASH}$"
_REQUEST_ID_PATTERN = rf"^retrieval-request-v3:sha256:{_HASH}$"
_RESULT_ID_PATTERN = rf"^retrieval-shadow-result:sha256:{_HASH}$"
_INDEX_VERSION_PATTERN = rf"^knowledge-index:sha256:{_HASH}$"
_BUILD_ID_PATTERN = (
    rf"^(?:published-knowledge|evaluation-knowledge|retrieval-fixture):sha256:{_HASH}$"
)
_SOURCE_BUNDLE_ID_PATTERN = rf"^source-bundle:sha256:{_HASH}$"
_CONFIG_ID_PATTERN = rf"^retrieval-config:sha256:{_HASH}$"
_POLICY_ID_PATTERN = rf"^retrieval-shadow-policy:sha256:{_HASH}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _parse_sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in values
    ):
        raise ValueError(f"{context} must contain non-empty trimmed text")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _rounded_ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


class RatioMetricV1(_FrozenModel):
    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(ge=0)]
    value: Annotated[float | None, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def validate_ratio(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed denominator")
        if self.value != _rounded_ratio(self.numerator, self.denominator):
            raise ValueError("metric value does not match numerator and denominator")
        return self


class MeanMetricV1(_FrozenModel):
    value_sum: Annotated[float, Field(ge=0.0)]
    value_count: Annotated[int, Field(ge=0)]
    value: Annotated[float | None, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def validate_mean(self) -> Self:
        rounded_sum = round(self.value_sum, 8)
        if rounded_sum > self.value_count:
            raise ValueError("mean value sum cannot exceed its count")
        expected = None if self.value_count == 0 else round(rounded_sum / self.value_count, 6)
        if self.value_sum != rounded_sum or self.value != expected:
            raise ValueError("mean metric does not rebuild from value sum and count")
        return self


class RankingMetricsAtKV1(_FrozenModel):
    k: MetricK
    retriever_required_recall: RatioMetricV1
    full_chain_required_coverage: RatioMetricV1
    precision: RatioMetricV1
    critical_dimension_coverage: RatioMetricV1


class RankingEvaluationV1(_FrozenModel):
    scope: RankingScope
    ordered_rule_ids: tuple[str, ...]
    metrics_at_k: tuple[RankingMetricsAtKV1, ...]
    retriever_required_reciprocal_rank: Annotated[float | None, Field(ge=0.0, le=1.0)]
    full_chain_required_reciprocal_rank: Annotated[float | None, Field(ge=0.0, le=1.0)]
    forbidden_hit_rule_ids: tuple[str, ...]
    applicability_violation_rule_ids: tuple[str, ...]

    @field_validator(
        "ordered_rule_ids",
        "metrics_at_k",
        "forbidden_hit_rule_ids",
        "applicability_violation_rule_ids",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Ranking Evaluation collections")

    @model_validator(mode="after")
    def validate_ranking(self) -> Self:
        if any(not item or item != item.strip() for item in self.ordered_rule_ids):
            raise ValueError("Ranking rule IDs must be non-empty trimmed text")
        if len(self.ordered_rule_ids) != len(set(self.ordered_rule_ids)):
            raise ValueError("Ranking rule IDs must be unique")
        if tuple(item.k for item in self.metrics_at_k) != METRIC_K_VALUES:
            raise ValueError("Ranking metrics must use the frozen K order")
        for values, context in (
            (self.forbidden_hit_rule_ids, "forbidden hits"),
            (self.applicability_violation_rule_ids, "applicability violations"),
        ):
            _validate_strings(values, f"Ranking Evaluation {context}")
            if not set(values).issubset(self.ordered_rule_ids):
                raise ValueError(f"Ranking Evaluation {context} must come from the ranking")
        return self


class ArmUnitEvaluationV1(_FrozenModel):
    arm: ShadowArmId
    pre_budget: RankingEvaluationV1
    selected: RankingEvaluationV1
    truth_case_kind: TruthCaseKind
    formal_execution_status: FormalExecutionStatus
    diagnostic_codes: tuple[ShadowDiagnosticCode, ...]
    degraded: bool
    candidate_empty: bool
    selected_empty: bool
    used_tokens: Annotated[int, Field(ge=0)]
    token_budget: Annotated[int, Field(ge=1)]
    token_budget_utilization: RatioMetricV1

    @field_validator("diagnostic_codes", mode="before")
    @classmethod
    def parse_diagnostics(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Arm diagnostic codes")

    @model_validator(mode="after")
    def validate_arm(self) -> Self:
        if self.pre_budget.scope != "post_fusion_pre_budget":
            raise ValueError("pre-budget evaluation uses an invalid scope")
        if self.selected.scope != "post_budget_selected":
            raise ValueError("selected evaluation uses an invalid scope")
        _validate_strings(self.diagnostic_codes, "Arm diagnostic codes")
        if self.degraded != any(item in _DEGRADED_CODES for item in self.diagnostic_codes):
            raise ValueError("Arm degraded state differs from diagnostics")
        if self.candidate_empty != (not self.pre_budget.ordered_rule_ids):
            raise ValueError("Arm candidate empty state differs from ranking")
        if self.selected_empty != (not self.selected.ordered_rule_ids):
            raise ValueError("Arm selected empty state differs from ranking")
        selected_ids = set(self.selected.ordered_rule_ids)
        if (
            tuple(
                rule_id for rule_id in self.pre_budget.ordered_rule_ids if rule_id in selected_ids
            )
            != self.selected.ordered_rule_ids
        ):
            raise ValueError("selected ranking must be a stable subset of pre-budget ranking")
        if self.used_tokens > self.token_budget:
            raise ValueError("Arm token use exceeds its budget")
        if (
            self.token_budget_utilization.numerator != self.used_tokens
            or self.token_budget_utilization.denominator != self.token_budget
        ):
            raise ValueError("Arm token utilization differs from token use")
        return self


class MetricDeltaAtKV1(_FrozenModel):
    k: MetricK
    retriever_required_recall_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    full_chain_required_coverage_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    precision_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    critical_dimension_coverage_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]

    @field_validator(
        "retriever_required_recall_delta",
        "full_chain_required_coverage_delta",
        "precision_delta",
        "critical_dimension_coverage_delta",
    )
    @classmethod
    def validate_rounded_delta(cls, value: float | None) -> float | None:
        if value is not None and value != round(value, 6):
            raise ValueError("metric deltas must use six decimal places")
        return value


class UnitArmComparisonV1(_FrozenModel):
    pre_budget_added_rule_ids: tuple[str, ...]
    pre_budget_removed_rule_ids: tuple[str, ...]
    selected_added_rule_ids: tuple[str, ...]
    selected_removed_rule_ids: tuple[str, ...]
    pre_budget_metric_deltas: tuple[MetricDeltaAtKV1, ...]
    selected_metric_deltas: tuple[MetricDeltaAtKV1, ...]
    pre_budget_retriever_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    pre_budget_full_chain_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    selected_retriever_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    selected_full_chain_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    observed_pre_budget_forbidden_hit_delta: int
    observed_selected_forbidden_hit_delta: int
    observed_pre_budget_applicability_violation_delta: int
    observed_selected_applicability_violation_delta: int
    comparison_eligible: bool
    exclusion_reasons: tuple[ComparisonExclusionReason, ...]

    @field_validator(
        "pre_budget_added_rule_ids",
        "pre_budget_removed_rule_ids",
        "selected_added_rule_ids",
        "selected_removed_rule_ids",
        "pre_budget_metric_deltas",
        "selected_metric_deltas",
        "exclusion_reasons",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Arm comparison collections")

    @field_validator(
        "pre_budget_retriever_required_mrr_delta",
        "pre_budget_full_chain_required_mrr_delta",
        "selected_retriever_required_mrr_delta",
        "selected_full_chain_required_mrr_delta",
    )
    @classmethod
    def validate_rounded_mrr_delta(cls, value: float | None) -> float | None:
        if value is not None and value != round(value, 6):
            raise ValueError("MRR deltas must use six decimal places")
        return value

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        for values, context in (
            (self.pre_budget_added_rule_ids, "pre-budget additions"),
            (self.pre_budget_removed_rule_ids, "pre-budget removals"),
            (self.selected_added_rule_ids, "selected additions"),
            (self.selected_removed_rule_ids, "selected removals"),
        ):
            _validate_strings(values, f"Arm comparison {context}")
        if tuple(item.k for item in self.pre_budget_metric_deltas) != METRIC_K_VALUES:
            raise ValueError("pre-budget delta metrics use an invalid K order")
        if tuple(item.k for item in self.selected_metric_deltas) != METRIC_K_VALUES:
            raise ValueError("selected delta metrics use an invalid K order")
        if self.exclusion_reasons != tuple(sorted(set(self.exclusion_reasons))):
            raise ValueError("comparison exclusion reasons must be sorted and unique")
        if self.comparison_eligible != (not self.exclusion_reasons):
            raise ValueError("comparison eligibility differs from exclusion reasons")
        if not self.comparison_eligible:
            quality_deltas = (
                *(
                    value
                    for item in self.pre_budget_metric_deltas
                    for value in (
                        item.retriever_required_recall_delta,
                        item.full_chain_required_coverage_delta,
                        item.precision_delta,
                        item.critical_dimension_coverage_delta,
                    )
                ),
                *(
                    value
                    for item in self.selected_metric_deltas
                    for value in (
                        item.retriever_required_recall_delta,
                        item.full_chain_required_coverage_delta,
                        item.precision_delta,
                        item.critical_dimension_coverage_delta,
                    )
                ),
                self.pre_budget_retriever_required_mrr_delta,
                self.pre_budget_full_chain_required_mrr_delta,
                self.selected_retriever_required_mrr_delta,
                self.selected_full_chain_required_mrr_delta,
            )
            if any(value is not None for value in quality_deltas):
                raise ValueError("excluded comparisons cannot publish quality deltas")
        return self


class RetrievalShadowUnitEvaluationV1(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    source_ref_id: Annotated[str, Field(pattern=rf"^code-source:sha256:{_HASH}$")]
    profile_id: Annotated[str, Field(pattern=rf"^feature-profile:sha256:{_HASH}$")]
    split: DocumentTruthSplit
    index_eligible_required_rule_ids: tuple[str, ...]
    missing_required_rule_ids: tuple[str, ...]
    knowledge_gap: RatioMetricV1
    runtime_formal_dimension_coverage: RatioMetricV1
    candidate_only_dimension_coverage: RatioMetricV1
    arms: tuple[ArmUnitEvaluationV1, ...]
    comparison: UnitArmComparisonV1

    @field_validator(
        "index_eligible_required_rule_ids",
        "missing_required_rule_ids",
        "arms",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Unit Evaluation collections")

    @model_validator(mode="after")
    def validate_unit(self) -> Self:
        _validate_strings(
            self.index_eligible_required_rule_ids,
            "Unit index-eligible required rules",
        )
        _validate_strings(self.missing_required_rule_ids, "Unit missing required rules")
        if set(self.index_eligible_required_rule_ids).intersection(self.missing_required_rule_ids):
            raise ValueError("Unit required eligibility partitions overlap")
        if self.knowledge_gap.numerator != len(
            self.missing_required_rule_ids
        ) or self.knowledge_gap.denominator != len(self.index_eligible_required_rule_ids) + len(
            self.missing_required_rule_ids
        ):
            raise ValueError("Unit knowledge gap differs from required rule partition")
        if tuple(item.arm for item in self.arms) != ("static_vector", "hybrid"):
            raise ValueError("Unit Evaluation requires static and hybrid arms in order")
        static, hybrid = self.arms
        if (
            static.truth_case_kind != hybrid.truth_case_kind
            or static.formal_execution_status != hybrid.formal_execution_status
            or static.token_budget != hybrid.token_budget
        ):
            raise ValueError("Unit arms do not share their evaluation roots")
        if self.comparison != _unit_comparison(static, hybrid):
            raise ValueError("Unit comparison does not rebuild from its arms")
        return self


class AggregateRankingEvaluationV1(_FrozenModel):
    scope: RankingScope
    metrics_at_k: tuple[RankingMetricsAtKV1, ...]
    retriever_required_mrr: MeanMetricV1
    full_chain_required_mrr: MeanMetricV1
    forbidden_hit_count: Annotated[int, Field(ge=0)]
    applicability_violation_count: Annotated[int, Field(ge=0)]
    empty_unit_count: Annotated[int, Field(ge=0)]

    @field_validator("metrics_at_k", mode="before")
    @classmethod
    def parse_metrics(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Aggregate ranking metrics")

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        if tuple(item.k for item in self.metrics_at_k) != METRIC_K_VALUES:
            raise ValueError("Aggregate ranking metrics use an invalid K order")
        return self


class ArmAggregateV1(_FrozenModel):
    arm: ShadowArmId
    pre_budget: AggregateRankingEvaluationV1
    selected: AggregateRankingEvaluationV1
    degraded_unit_count: Annotated[int, Field(ge=0)]
    used_tokens: Annotated[int, Field(ge=0)]
    token_budget: Annotated[int, Field(ge=1)]
    token_budget_utilization: RatioMetricV1

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        if self.pre_budget.scope != "post_fusion_pre_budget":
            raise ValueError("aggregate pre-budget scope is invalid")
        if self.selected.scope != "post_budget_selected":
            raise ValueError("aggregate selected scope is invalid")
        if self.used_tokens > self.token_budget:
            raise ValueError("aggregate token use exceeds its budget")
        if (
            self.token_budget_utilization.numerator != self.used_tokens
            or self.token_budget_utilization.denominator != self.token_budget
        ):
            raise ValueError("aggregate token utilization differs from token use")
        return self


class AggregateArmComparisonV1(_FrozenModel):
    pre_budget_metric_deltas: tuple[MetricDeltaAtKV1, ...]
    selected_metric_deltas: tuple[MetricDeltaAtKV1, ...]
    pre_budget_retriever_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    pre_budget_full_chain_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    selected_retriever_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    selected_full_chain_required_mrr_delta: Annotated[float | None, Field(ge=-1.0, le=1.0)]
    observed_pre_budget_forbidden_hit_delta: int
    observed_selected_forbidden_hit_delta: int
    observed_pre_budget_applicability_violation_delta: int
    observed_selected_applicability_violation_delta: int
    pre_budget_added_unit_rule_count: Annotated[int, Field(ge=0)]
    pre_budget_removed_unit_rule_count: Annotated[int, Field(ge=0)]
    selected_added_unit_rule_count: Annotated[int, Field(ge=0)]
    selected_removed_unit_rule_count: Annotated[int, Field(ge=0)]
    comparable_unit_count: Annotated[int, Field(ge=0)]
    excluded_unit_count: Annotated[int, Field(ge=0)]

    @field_validator("pre_budget_metric_deltas", "selected_metric_deltas", mode="before")
    @classmethod
    def parse_metrics(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Aggregate comparison metrics")

    @field_validator(
        "pre_budget_retriever_required_mrr_delta",
        "pre_budget_full_chain_required_mrr_delta",
        "selected_retriever_required_mrr_delta",
        "selected_full_chain_required_mrr_delta",
    )
    @classmethod
    def validate_rounded_mrr_delta(cls, value: float | None) -> float | None:
        if value is not None and value != round(value, 6):
            raise ValueError("aggregate MRR deltas must use six decimal places")
        return value

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        if tuple(item.k for item in self.pre_budget_metric_deltas) != METRIC_K_VALUES:
            raise ValueError("aggregate pre-budget deltas use an invalid K order")
        if tuple(item.k for item in self.selected_metric_deltas) != METRIC_K_VALUES:
            raise ValueError("aggregate selected deltas use an invalid K order")
        if self.comparable_unit_count == 0:
            quality_deltas = (
                *(
                    value
                    for item in self.pre_budget_metric_deltas
                    for value in (
                        item.retriever_required_recall_delta,
                        item.full_chain_required_coverage_delta,
                        item.precision_delta,
                        item.critical_dimension_coverage_delta,
                    )
                ),
                *(
                    value
                    for item in self.selected_metric_deltas
                    for value in (
                        item.retriever_required_recall_delta,
                        item.full_chain_required_coverage_delta,
                        item.precision_delta,
                        item.critical_dimension_coverage_delta,
                    )
                ),
                self.pre_budget_retriever_required_mrr_delta,
                self.pre_budget_full_chain_required_mrr_delta,
                self.selected_retriever_required_mrr_delta,
                self.selected_full_chain_required_mrr_delta,
            )
            if any(value is not None for value in quality_deltas):
                raise ValueError("empty comparable population cannot publish quality deltas")
        return self


class SplitAggregateV1(_FrozenModel):
    split: DocumentTruthSplit
    unit_count: Annotated[int, Field(ge=1, le=50)]
    arm_aggregates: tuple[ArmAggregateV1, ...]
    arm_comparison: AggregateArmComparisonV1
    knowledge_gap: RatioMetricV1
    runtime_formal_dimension_coverage: RatioMetricV1
    candidate_only_dimension_coverage: RatioMetricV1

    @field_validator("arm_aggregates", mode="before")
    @classmethod
    def parse_aggregates(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Split arm aggregates")

    @model_validator(mode="after")
    def validate_split_aggregate(self) -> Self:
        if tuple(item.arm for item in self.arm_aggregates) != ("static_vector", "hybrid"):
            raise ValueError("Split requires static and hybrid aggregates in order")
        if (
            self.arm_comparison.comparable_unit_count + self.arm_comparison.excluded_unit_count
            != self.unit_count
        ):
            raise ValueError("Split comparison coverage does not match Unit count")
        return self


class _RetrievalShadowEvaluationPayloadV1(_FrozenModel):
    schema_version: Literal["retrieval-shadow-evaluation-v1"]
    truth_id: Annotated[str, Field(pattern=_TRUTH_ID_PATTERN)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID_PATTERN)]
    shadow_result_id: Annotated[str, Field(pattern=_RESULT_ID_PATTERN)]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    knowledge_build_id: Annotated[str, Field(pattern=_BUILD_ID_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    base_retrieval_config_fingerprint: Annotated[str, Field(pattern=_CONFIG_ID_PATTERN)]
    shadow_policy_fingerprint: Annotated[str, Field(pattern=_POLICY_ID_PATTERN)]
    unit_evaluations: tuple[RetrievalShadowUnitEvaluationV1, ...]
    arm_aggregates: tuple[ArmAggregateV1, ...]
    arm_comparison: AggregateArmComparisonV1
    split_aggregates: tuple[SplitAggregateV1, ...]
    unit_count: Annotated[int, Field(ge=1, le=50)]
    development_unit_count: Annotated[int, Field(ge=0)]
    calibration_unit_count: Annotated[int, Field(ge=0)]
    knowledge_gap: RatioMetricV1
    runtime_formal_dimension_coverage: RatioMetricV1
    candidate_only_dimension_coverage: RatioMetricV1
    metric_aggregation: Literal["micro_counts_and_unit_mean_mrr"]
    evaluation_scope: Literal["relative_gain_on_fixed_index"]
    verification_root_scope: Literal["caller_supplied_self_hashed_truth_request_result_and_index"]
    authority_status: Literal["serialized_audit_only"]
    downstream_use: Literal["offline_relative_evaluation_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    user_visible: Literal[False]
    prompt_eligible: Literal[False]
    finding_evidence_eligible: Literal[False]
    qualification_blockers: tuple[str, ...]

    @field_validator(
        "unit_evaluations",
        "arm_aggregates",
        "split_aggregates",
        "qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Retrieval Shadow Evaluation collections")

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        unit_ids = tuple(item.unit_id for item in self.unit_evaluations)
        if unit_ids != tuple(sorted(set(unit_ids))) or len(unit_ids) != self.unit_count:
            raise ValueError("Evaluation Units must be sorted, unique, and match unit_count")
        if self.development_unit_count != sum(
            item.split == "development" for item in self.unit_evaluations
        ):
            raise ValueError("development Unit count does not rebuild")
        if self.calibration_unit_count != sum(
            item.split == "calibration" for item in self.unit_evaluations
        ):
            raise ValueError("calibration Unit count does not rebuild")
        if self.development_unit_count + self.calibration_unit_count != self.unit_count:
            raise ValueError("evaluation split counts do not cover all Units")
        if tuple(item.arm for item in self.arm_aggregates) != ("static_vector", "hybrid"):
            raise ValueError("Evaluation requires static and hybrid aggregates in order")
        if self.qualification_blockers != _QUALIFICATION_BLOCKERS:
            raise ValueError("Evaluation must retain all qualification blockers")
        if (
            self.arm_comparison.comparable_unit_count + self.arm_comparison.excluded_unit_count
            != self.unit_count
        ):
            raise ValueError("comparison coverage does not match Unit count")
        expected_splits = tuple(
            split
            for split in DOCUMENT_TRUTH_SPLITS
            if any(item.split == split for item in self.unit_evaluations)
        )
        if tuple(item.split for item in self.split_aggregates) != expected_splits:
            raise ValueError("split aggregates do not match the populated frozen split order")
        if self.arm_aggregates != (
            _arm_aggregate("static_vector", self.unit_evaluations),
            _arm_aggregate("hybrid", self.unit_evaluations),
        ):
            raise ValueError("global arm aggregates do not rebuild from Units")
        if self.arm_comparison != _aggregate_comparison(self.unit_evaluations):
            raise ValueError("global arm comparison does not rebuild from Units")
        expected_ratios = _aggregate_unit_ratios(self.unit_evaluations)
        if (
            self.knowledge_gap,
            self.runtime_formal_dimension_coverage,
            self.candidate_only_dimension_coverage,
        ) != expected_ratios:
            raise ValueError("global evaluation ratios do not rebuild from Units")
        expected_split_aggregates = tuple(
            _split_aggregate(
                split,
                tuple(item for item in self.unit_evaluations if item.split == split),
            )
            for split in expected_splits
        )
        if self.split_aggregates != expected_split_aggregates:
            raise ValueError("split aggregates do not rebuild from Units")
        return self


class RetrievalShadowEvaluationReportV1(_RetrievalShadowEvaluationPayloadV1):
    report_id: Annotated[str, Field(pattern=_REPORT_ID_PATTERN)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = _canonical_hash(
            "retrieval-shadow-evaluation",
            self.model_dump(mode="json", exclude={"report_id"}),
        )
        if self.report_id != expected:
            raise ValueError("Retrieval Shadow Evaluation ID does not match content")
        return self


@dataclass(frozen=True)
class RetrievalShadowEvaluationInputV1:
    truth: RetrievalDocumentTruthV1
    request: RetrievalRequestV3
    result: RetrievalShadowResultV3
    index: KnowledgeIndex


def _ratio(numerator: int, denominator: int) -> RatioMetricV1:
    return RatioMetricV1(
        numerator=numerator,
        denominator=denominator,
        value=_rounded_ratio(numerator, denominator),
    )


def _mean(values: tuple[float | None, ...]) -> MeanMetricV1:
    present = tuple(item for item in values if item is not None)
    value_sum = round(sum(present), 8)
    return MeanMetricV1(
        value_sum=value_sum,
        value_count=len(present),
        value=None if not present else round(value_sum / len(present), 6),
    )


def _reciprocal_rank(
    ordered_rule_ids: tuple[str, ...],
    relevant_rule_ids: frozenset[str],
) -> float | None:
    if not relevant_rule_ids:
        return None
    for rank, rule_id in enumerate(ordered_rule_ids, start=1):
        if rule_id in relevant_rule_ids:
            return round(1.0 / rank, 6)
    return 0.0


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else round(right - left, 6)


def _metric_deltas(
    static: tuple[RankingMetricsAtKV1, ...],
    hybrid: tuple[RankingMetricsAtKV1, ...],
    *,
    comparison_eligible: bool = True,
) -> tuple[MetricDeltaAtKV1, ...]:
    return tuple(
        MetricDeltaAtKV1(
            k=static_item.k,
            retriever_required_recall_delta=(
                _delta(
                    static_item.retriever_required_recall.value,
                    hybrid_item.retriever_required_recall.value,
                )
                if comparison_eligible
                else None
            ),
            full_chain_required_coverage_delta=(
                _delta(
                    static_item.full_chain_required_coverage.value,
                    hybrid_item.full_chain_required_coverage.value,
                )
                if comparison_eligible
                else None
            ),
            precision_delta=(
                _delta(
                    static_item.precision.value,
                    hybrid_item.precision.value,
                )
                if comparison_eligible
                else None
            ),
            critical_dimension_coverage_delta=(
                _delta(
                    static_item.critical_dimension_coverage.value,
                    hybrid_item.critical_dimension_coverage.value,
                )
                if comparison_eligible
                else None
            ),
        )
        for static_item, hybrid_item in zip(static, hybrid, strict=True)
    )


def _ranking_evaluation(
    *,
    scope: RankingScope,
    ordered_rule_ids: tuple[str, ...],
    truth_by_rule: Mapping[str, DocumentTruthClauseV1],
    index_by_rule: Mapping[str, KnowledgeIndexRecord],
    index_eligible_required: frozenset[str],
    all_required: frozenset[str],
    relevant_rule_ids: frozenset[str],
    critical_dimension_ids: frozenset[str],
    target_platform: TargetPlatform,
) -> RankingEvaluationV1:
    metrics: list[RankingMetricsAtKV1] = []
    for k in METRIC_K_VALUES:
        top = ordered_rule_ids[:k]
        top_set = set(top)
        covered_dimensions: set[str] = set()
        for rule_id in top:
            if rule_id not in relevant_rule_ids:
                continue
            record = index_by_rule[rule_id]
            covered_dimensions.update(record.annotation.dimension_ids)
        metrics.append(
            RankingMetricsAtKV1(
                k=k,
                retriever_required_recall=_ratio(
                    len(top_set.intersection(index_eligible_required)),
                    len(index_eligible_required),
                ),
                full_chain_required_coverage=_ratio(
                    len(top_set.intersection(all_required)),
                    len(all_required),
                ),
                precision=_ratio(
                    len(top_set.intersection(relevant_rule_ids)),
                    len(top),
                ),
                critical_dimension_coverage=_ratio(
                    len(covered_dimensions.intersection(critical_dimension_ids)),
                    len(critical_dimension_ids),
                ),
            )
        )
    forbidden = tuple(
        sorted(
            rule_id
            for rule_id in ordered_rule_ids
            if truth_by_rule[rule_id].relevance == "forbidden"
        )
    )
    applicability_violations = tuple(
        sorted(
            rule_id
            for rule_id in ordered_rule_ids
            if evaluate_applicability(
                index_by_rule[rule_id].clause.applicability,
                target_platform,
            ).decision
            == "excluded"
        )
    )
    return RankingEvaluationV1(
        scope=scope,
        ordered_rule_ids=ordered_rule_ids,
        metrics_at_k=tuple(metrics),
        retriever_required_reciprocal_rank=_reciprocal_rank(
            ordered_rule_ids,
            index_eligible_required,
        ),
        full_chain_required_reciprocal_rank=_reciprocal_rank(
            ordered_rule_ids,
            all_required,
        ),
        forbidden_hit_rule_ids=forbidden,
        applicability_violation_rule_ids=applicability_violations,
    )


def _truth_case_kind(
    index_eligible_required: frozenset[str],
    missing_required: frozenset[str],
    all_acceptable: frozenset[str],
) -> TruthCaseKind:
    if index_eligible_required:
        return "index_eligible_required"
    if missing_required:
        return "knowledge_gap_required"
    if all_acceptable:
        return "acceptable_only"
    return "true_negative"


def _arm_unit_evaluation(
    *,
    arm: ShadowArmResultV3,
    formal_execution_status: FormalExecutionStatus,
    truth_by_rule: Mapping[str, DocumentTruthClauseV1],
    index_by_rule: Mapping[str, KnowledgeIndexRecord],
    index_eligible_required: frozenset[str],
    missing_required: frozenset[str],
    all_required: frozenset[str],
    relevant_rule_ids: frozenset[str],
    all_acceptable: frozenset[str],
    critical_dimension_ids: frozenset[str],
    target_platform: TargetPlatform,
) -> ArmUnitEvaluationV1:
    pre_budget_ids = tuple(item.rule_id for item in arm.ranked_clauses)
    selected_ids = arm.selected_rule_ids
    diagnostic_codes = tuple(sorted({item.code for item in arm.diagnostics}))
    return ArmUnitEvaluationV1(
        arm=arm.arm,
        pre_budget=_ranking_evaluation(
            scope="post_fusion_pre_budget",
            ordered_rule_ids=pre_budget_ids,
            truth_by_rule=truth_by_rule,
            index_by_rule=index_by_rule,
            index_eligible_required=index_eligible_required,
            all_required=all_required,
            relevant_rule_ids=relevant_rule_ids,
            critical_dimension_ids=critical_dimension_ids,
            target_platform=target_platform,
        ),
        selected=_ranking_evaluation(
            scope="post_budget_selected",
            ordered_rule_ids=selected_ids,
            truth_by_rule=truth_by_rule,
            index_by_rule=index_by_rule,
            index_eligible_required=index_eligible_required,
            all_required=all_required,
            relevant_rule_ids=relevant_rule_ids,
            critical_dimension_ids=critical_dimension_ids,
            target_platform=target_platform,
        ),
        truth_case_kind=_truth_case_kind(
            index_eligible_required,
            missing_required,
            all_acceptable,
        ),
        formal_execution_status=formal_execution_status,
        diagnostic_codes=diagnostic_codes,
        degraded=any(item in _DEGRADED_CODES for item in diagnostic_codes),
        candidate_empty=not pre_budget_ids,
        selected_empty=not selected_ids,
        used_tokens=arm.used_tokens,
        token_budget=arm.token_budget,
        token_budget_utilization=_ratio(arm.used_tokens, arm.token_budget),
    )


def _unit_comparison(
    static: ArmUnitEvaluationV1,
    hybrid: ArmUnitEvaluationV1,
) -> UnitArmComparisonV1:
    static_pre = set(static.pre_budget.ordered_rule_ids)
    hybrid_pre = set(hybrid.pre_budget.ordered_rule_ids)
    static_selected = set(static.selected.ordered_rule_ids)
    hybrid_selected = set(hybrid.selected.ordered_rule_ids)
    reasons: set[ComparisonExclusionReason] = set()
    if static.formal_execution_status != "valid_result":
        reasons.add("formal_execution_not_valid")
    if static.degraded or hybrid.degraded:
        reasons.add("runtime_degraded")
    comparison_eligible = not reasons
    return UnitArmComparisonV1(
        pre_budget_added_rule_ids=tuple(sorted(hybrid_pre - static_pre)),
        pre_budget_removed_rule_ids=tuple(sorted(static_pre - hybrid_pre)),
        selected_added_rule_ids=tuple(sorted(hybrid_selected - static_selected)),
        selected_removed_rule_ids=tuple(sorted(static_selected - hybrid_selected)),
        pre_budget_metric_deltas=_metric_deltas(
            static.pre_budget.metrics_at_k,
            hybrid.pre_budget.metrics_at_k,
            comparison_eligible=comparison_eligible,
        ),
        selected_metric_deltas=_metric_deltas(
            static.selected.metrics_at_k,
            hybrid.selected.metrics_at_k,
            comparison_eligible=comparison_eligible,
        ),
        pre_budget_retriever_required_mrr_delta=(
            _delta(
                static.pre_budget.retriever_required_reciprocal_rank,
                hybrid.pre_budget.retriever_required_reciprocal_rank,
            )
            if comparison_eligible
            else None
        ),
        pre_budget_full_chain_required_mrr_delta=(
            _delta(
                static.pre_budget.full_chain_required_reciprocal_rank,
                hybrid.pre_budget.full_chain_required_reciprocal_rank,
            )
            if comparison_eligible
            else None
        ),
        selected_retriever_required_mrr_delta=(
            _delta(
                static.selected.retriever_required_reciprocal_rank,
                hybrid.selected.retriever_required_reciprocal_rank,
            )
            if comparison_eligible
            else None
        ),
        selected_full_chain_required_mrr_delta=(
            _delta(
                static.selected.full_chain_required_reciprocal_rank,
                hybrid.selected.full_chain_required_reciprocal_rank,
            )
            if comparison_eligible
            else None
        ),
        observed_pre_budget_forbidden_hit_delta=(
            len(hybrid.pre_budget.forbidden_hit_rule_ids)
            - len(static.pre_budget.forbidden_hit_rule_ids)
        ),
        observed_selected_forbidden_hit_delta=(
            len(hybrid.selected.forbidden_hit_rule_ids)
            - len(static.selected.forbidden_hit_rule_ids)
        ),
        observed_pre_budget_applicability_violation_delta=(
            len(hybrid.pre_budget.applicability_violation_rule_ids)
            - len(static.pre_budget.applicability_violation_rule_ids)
        ),
        observed_selected_applicability_violation_delta=(
            len(hybrid.selected.applicability_violation_rule_ids)
            - len(static.selected.applicability_violation_rule_ids)
        ),
        comparison_eligible=comparison_eligible,
        exclusion_reasons=tuple(sorted(reasons)),
    )


def _aggregate_ranking(
    *,
    scope: RankingScope,
    rankings: tuple[RankingEvaluationV1, ...],
) -> AggregateRankingEvaluationV1:
    metrics = tuple(
        RankingMetricsAtKV1(
            k=k,
            retriever_required_recall=_ratio(
                sum(
                    item.metrics_at_k[index].retriever_required_recall.numerator
                    for item in rankings
                ),
                sum(
                    item.metrics_at_k[index].retriever_required_recall.denominator
                    for item in rankings
                ),
            ),
            full_chain_required_coverage=_ratio(
                sum(
                    item.metrics_at_k[index].full_chain_required_coverage.numerator
                    for item in rankings
                ),
                sum(
                    item.metrics_at_k[index].full_chain_required_coverage.denominator
                    for item in rankings
                ),
            ),
            precision=_ratio(
                sum(item.metrics_at_k[index].precision.numerator for item in rankings),
                sum(item.metrics_at_k[index].precision.denominator for item in rankings),
            ),
            critical_dimension_coverage=_ratio(
                sum(
                    item.metrics_at_k[index].critical_dimension_coverage.numerator
                    for item in rankings
                ),
                sum(
                    item.metrics_at_k[index].critical_dimension_coverage.denominator
                    for item in rankings
                ),
            ),
        )
        for index, k in enumerate(METRIC_K_VALUES)
    )
    return AggregateRankingEvaluationV1(
        scope=scope,
        metrics_at_k=metrics,
        retriever_required_mrr=_mean(
            tuple(item.retriever_required_reciprocal_rank for item in rankings)
        ),
        full_chain_required_mrr=_mean(
            tuple(item.full_chain_required_reciprocal_rank for item in rankings)
        ),
        forbidden_hit_count=sum(len(item.forbidden_hit_rule_ids) for item in rankings),
        applicability_violation_count=sum(
            len(item.applicability_violation_rule_ids) for item in rankings
        ),
        empty_unit_count=sum(not item.ordered_rule_ids for item in rankings),
    )


def _arm_aggregate(
    arm_id: ShadowArmId,
    units: tuple[RetrievalShadowUnitEvaluationV1, ...],
) -> ArmAggregateV1:
    arms = tuple(next(item for item in unit.arms if item.arm == arm_id) for unit in units)
    used_tokens = sum(item.used_tokens for item in arms)
    token_budget = sum(item.token_budget for item in arms)
    return ArmAggregateV1(
        arm=arm_id,
        pre_budget=_aggregate_ranking(
            scope="post_fusion_pre_budget",
            rankings=tuple(item.pre_budget for item in arms),
        ),
        selected=_aggregate_ranking(
            scope="post_budget_selected",
            rankings=tuple(item.selected for item in arms),
        ),
        degraded_unit_count=sum(item.degraded for item in arms),
        used_tokens=used_tokens,
        token_budget=token_budget,
        token_budget_utilization=_ratio(used_tokens, token_budget),
    )


def _aggregate_unit_ratios(
    units: tuple[RetrievalShadowUnitEvaluationV1, ...],
) -> tuple[RatioMetricV1, RatioMetricV1, RatioMetricV1]:
    return (
        _ratio(
            sum(item.knowledge_gap.numerator for item in units),
            sum(item.knowledge_gap.denominator for item in units),
        ),
        _ratio(
            sum(item.runtime_formal_dimension_coverage.numerator for item in units),
            sum(item.runtime_formal_dimension_coverage.denominator for item in units),
        ),
        _ratio(
            sum(item.candidate_only_dimension_coverage.numerator for item in units),
            sum(item.candidate_only_dimension_coverage.denominator for item in units),
        ),
    )


def _aggregate_comparison(
    units: tuple[RetrievalShadowUnitEvaluationV1, ...],
) -> AggregateArmComparisonV1:
    comparable_units = tuple(item for item in units if item.comparison.comparison_eligible)
    comparisons = tuple(item.comparison for item in comparable_units)
    static_pre = _aggregate_ranking(
        scope="post_fusion_pre_budget",
        rankings=tuple(item.arms[0].pre_budget for item in comparable_units),
    )
    hybrid_pre = _aggregate_ranking(
        scope="post_fusion_pre_budget",
        rankings=tuple(item.arms[1].pre_budget for item in comparable_units),
    )
    static_selected = _aggregate_ranking(
        scope="post_budget_selected",
        rankings=tuple(item.arms[0].selected for item in comparable_units),
    )
    hybrid_selected = _aggregate_ranking(
        scope="post_budget_selected",
        rankings=tuple(item.arms[1].selected for item in comparable_units),
    )
    observed_static_pre = _aggregate_ranking(
        scope="post_fusion_pre_budget",
        rankings=tuple(item.arms[0].pre_budget for item in units),
    )
    observed_hybrid_pre = _aggregate_ranking(
        scope="post_fusion_pre_budget",
        rankings=tuple(item.arms[1].pre_budget for item in units),
    )
    observed_static_selected = _aggregate_ranking(
        scope="post_budget_selected",
        rankings=tuple(item.arms[0].selected for item in units),
    )
    observed_hybrid_selected = _aggregate_ranking(
        scope="post_budget_selected",
        rankings=tuple(item.arms[1].selected for item in units),
    )
    has_comparable_units = bool(comparable_units)
    return AggregateArmComparisonV1(
        pre_budget_metric_deltas=_metric_deltas(
            static_pre.metrics_at_k,
            hybrid_pre.metrics_at_k,
            comparison_eligible=has_comparable_units,
        ),
        selected_metric_deltas=_metric_deltas(
            static_selected.metrics_at_k,
            hybrid_selected.metrics_at_k,
            comparison_eligible=has_comparable_units,
        ),
        pre_budget_retriever_required_mrr_delta=(
            _delta(
                static_pre.retriever_required_mrr.value,
                hybrid_pre.retriever_required_mrr.value,
            )
            if has_comparable_units
            else None
        ),
        pre_budget_full_chain_required_mrr_delta=(
            _delta(
                static_pre.full_chain_required_mrr.value,
                hybrid_pre.full_chain_required_mrr.value,
            )
            if has_comparable_units
            else None
        ),
        selected_retriever_required_mrr_delta=(
            _delta(
                static_selected.retriever_required_mrr.value,
                hybrid_selected.retriever_required_mrr.value,
            )
            if has_comparable_units
            else None
        ),
        selected_full_chain_required_mrr_delta=(
            _delta(
                static_selected.full_chain_required_mrr.value,
                hybrid_selected.full_chain_required_mrr.value,
            )
            if has_comparable_units
            else None
        ),
        observed_pre_budget_forbidden_hit_delta=(
            observed_hybrid_pre.forbidden_hit_count - observed_static_pre.forbidden_hit_count
        ),
        observed_selected_forbidden_hit_delta=(
            observed_hybrid_selected.forbidden_hit_count
            - observed_static_selected.forbidden_hit_count
        ),
        observed_pre_budget_applicability_violation_delta=(
            observed_hybrid_pre.applicability_violation_count
            - observed_static_pre.applicability_violation_count
        ),
        observed_selected_applicability_violation_delta=(
            observed_hybrid_selected.applicability_violation_count
            - observed_static_selected.applicability_violation_count
        ),
        pre_budget_added_unit_rule_count=sum(
            len(item.pre_budget_added_rule_ids) for item in comparisons
        ),
        pre_budget_removed_unit_rule_count=sum(
            len(item.pre_budget_removed_rule_ids) for item in comparisons
        ),
        selected_added_unit_rule_count=sum(
            len(item.selected_added_rule_ids) for item in comparisons
        ),
        selected_removed_unit_rule_count=sum(
            len(item.selected_removed_rule_ids) for item in comparisons
        ),
        comparable_unit_count=len(comparable_units),
        excluded_unit_count=len(units) - len(comparable_units),
    )


def _split_aggregate(
    split: DocumentTruthSplit,
    units: tuple[RetrievalShadowUnitEvaluationV1, ...],
) -> SplitAggregateV1:
    if not units or any(item.split != split for item in units):
        raise ValueError("Split aggregate requires a non-empty matching Unit population")
    knowledge_gap, formal_coverage, candidate_coverage = _aggregate_unit_ratios(units)
    return SplitAggregateV1(
        split=split,
        unit_count=len(units),
        arm_aggregates=(
            _arm_aggregate("static_vector", units),
            _arm_aggregate("hybrid", units),
        ),
        arm_comparison=_aggregate_comparison(units),
        knowledge_gap=knowledge_gap,
        runtime_formal_dimension_coverage=formal_coverage,
        candidate_only_dimension_coverage=candidate_coverage,
    )


def _verify_roots(
    truth: RetrievalDocumentTruthV1,
    request: RetrievalRequestV3,
    result: RetrievalShadowResultV3,
    index: KnowledgeIndex,
) -> None:
    if (
        truth.index_version != request.index_version
        or request.index_version != result.index_version
        or result.index_version != index.index_version
    ):
        raise ValueError("Document evaluation roots use different KnowledgeIndex versions")
    if (
        truth.knowledge_build_id != result.knowledge_build_id
        or result.knowledge_build_id != index.published_build_id
        or truth.source_bundle_id != result.source_bundle_id
        or result.source_bundle_id != index.source_bundle_id
    ):
        raise ValueError("Document evaluation roots use different knowledge builds")
    if truth.target_platform != request.target_platform:
        raise ValueError("Document Truth target differs from Retrieval request")
    if result.verified_request_id != request.request_id:
        raise ValueError("Shadow Result differs from Retrieval request")
    if (
        truth.feature_config_version != request.feature_config_version
        or request.feature_config_version != index.feature_config_version
    ):
        raise ValueError("Document evaluation roots use different Feature Config versions")
    if result.base_retrieval_config_fingerprint != index.retrieval_config_fingerprint:
        raise ValueError("Shadow Result and KnowledgeIndex Retrieval configs differ")
    if result.index_origin != index.origin or result.embedding_version != index.embedding_version:
        raise ValueError("Shadow Result index metadata differs from KnowledgeIndex")

    truth_by_unit = {item.unit_id: item for item in truth.units}
    request_by_unit = {item.unit_id: item for item in request.units}
    result_by_unit = {item.unit_id: item for item in result.units}
    if set(truth_by_unit) != set(request_by_unit) or set(request_by_unit) != set(result_by_unit):
        raise ValueError("Document evaluation roots have different Unit coverage")

    index_by_rule = {item.clause.rule_id: item for item in index.records}
    for unit_id in sorted(request_by_unit):
        truth_unit = truth_by_unit[unit_id]
        request_unit = request_by_unit[unit_id]
        result_unit = result_by_unit[unit_id]
        if (
            truth_unit.source_ref_id != request_unit.source_ref_id
            or truth_unit.profile_id != request_unit.profile_id
            or result_unit.profile_id != request_unit.profile_id
            or result_unit.formal_hybrid_analysis_id != request_unit.formal_hybrid_analysis_id
            or result_unit.formal_execution_outcome_id != request_unit.formal_execution_outcome_id
            or result_unit.formal_ai_result_id != request_unit.formal_ai_result_id
            or result_unit.trusted_execution_subject_id != request_unit.trusted_execution_subject_id
            or result_unit.trusted_runner_attestation_id
            != request_unit.trusted_runner_attestation_id
            or result_unit.exact_tags != request_unit.exact_tags
            or result_unit.routing_tags != request_unit.routing_tags
            or result_unit.ai_inferred_tags != request_unit.ai_inferred_tags
            or result_unit.tag_disagreements != request_unit.tag_disagreements
            or result_unit.formal_dimension_ids != request_unit.retrieval_dimension_ids
            or result_unit.routing_dimension_ids != request_unit.routing_dimension_ids
            or result_unit.candidate_dimension_ids != request_unit.candidate_dimension_ids
            or result_unit.static_vector.token_budget != request_unit.knowledge_token_budget
            or result_unit.hybrid.token_budget != request_unit.knowledge_token_budget
        ):
            raise ValueError("Document evaluation Unit roots are not bound")

        truth_by_rule = {item.rule_id: item for item in truth_unit.clauses}
        observed_rule_ids = {
            clause.rule_id
            for arm in (result_unit.static_vector, result_unit.hybrid)
            for clause in arm.ranked_clauses
        }
        unlabelled = tuple(sorted(observed_rule_ids - set(truth_by_rule)))
        if unlabelled:
            raise ValueError(
                f"Shadow Result contains unlabelled Clauses for {unit_id}: {unlabelled!r}"
            )

        for truth_clause in truth_unit.clauses:
            record = index_by_rule.get(truth_clause.rule_id)
            if record is None:
                if truth_clause.relevance != "required":
                    raise ValueError(
                        "only required Document Truth may be absent from KnowledgeIndex"
                    )
                continue
            if (
                truth_clause.source_ref != record.clause.source_ref
                or truth_clause.heading_path != record.clause.heading_path
                or truth_clause.applicability != record.clause.applicability
                or truth_clause.rule_type != record.clause.rule_type
            ):
                raise ValueError("Document Truth Clause differs from KnowledgeIndex")

        for arm in (result_unit.static_vector, result_unit.hybrid):
            for clause in arm.ranked_clauses:
                record = index_by_rule.get(clause.rule_id)
                if record is None:
                    raise ValueError("Shadow Result Clause is absent from KnowledgeIndex")
                expected = (
                    record.clause.rule_type,
                    record.clause.status,
                    record.clause.text,
                    record.clause.heading_path,
                    record.clause.parent_context,
                    record.annotation.dimension_ids,
                    record.annotation.tags,
                    record.annotation.apis,
                    record.annotation.components,
                    record.annotation.decorators,
                    record.domains,
                    record.clause.source_ref,
                    len(
                        set(request_unit.retrieval_dimension_ids).intersection(
                            record.annotation.dimension_ids
                        )
                    ),
                    record.token_count,
                )
                actual = (
                    clause.rule_type,
                    clause.status,
                    clause.text,
                    clause.heading_path,
                    clause.parent_context,
                    clause.dimension_ids,
                    clause.tags,
                    clause.apis,
                    clause.components,
                    clause.decorators,
                    clause.domains,
                    clause.source_ref,
                    clause.formal_dimension_overlap,
                    clause.token_count,
                )
                if actual != expected:
                    raise ValueError("Shadow Result Clause differs from KnowledgeIndex")


def _build_unit_evaluation(
    truth_unit: DocumentTruthUnitV1,
    request_unit: RetrievalUnitRequestV3,
    result_unit: ShadowUnitComparisonV3,
    index: KnowledgeIndex,
    target_platform: TargetPlatform,
) -> RetrievalShadowUnitEvaluationV1:
    index_by_rule = {item.clause.rule_id: item for item in index.records}
    truth_by_rule = {item.rule_id: item for item in truth_unit.clauses}
    all_required = frozenset(
        item.rule_id for item in truth_unit.clauses if item.relevance == "required"
    )
    all_acceptable = frozenset(
        item.rule_id for item in truth_unit.clauses if item.relevance == "acceptable"
    )
    index_eligible_required = frozenset(
        rule_id
        for rule_id in all_required
        if rule_id in index_by_rule
        and evaluate_applicability(
            index_by_rule[rule_id].clause.applicability,
            target_platform,
        ).decision
        != "excluded"
    )
    missing_required = all_required - index_eligible_required
    relevant_rule_ids = frozenset(
        item.rule_id
        for item in truth_unit.clauses
        if item.relevance in {"required", "acceptable"}
        and item.rule_id in index_by_rule
        and evaluate_applicability(
            index_by_rule[item.rule_id].clause.applicability,
            target_platform,
        ).decision
        != "excluded"
    )
    critical_dimensions = frozenset(truth_unit.critical_dimension_ids)
    formal_dimensions = set(request_unit.retrieval_dimension_ids)
    candidate_only_dimensions = set(request_unit.candidate_dimension_ids) - formal_dimensions
    static = _arm_unit_evaluation(
        arm=result_unit.static_vector,
        formal_execution_status=result_unit.formal_execution_status,
        truth_by_rule=truth_by_rule,
        index_by_rule=index_by_rule,
        index_eligible_required=index_eligible_required,
        missing_required=missing_required,
        all_required=all_required,
        all_acceptable=all_acceptable,
        relevant_rule_ids=relevant_rule_ids,
        critical_dimension_ids=critical_dimensions,
        target_platform=target_platform,
    )
    hybrid = _arm_unit_evaluation(
        arm=result_unit.hybrid,
        formal_execution_status=result_unit.formal_execution_status,
        truth_by_rule=truth_by_rule,
        index_by_rule=index_by_rule,
        index_eligible_required=index_eligible_required,
        missing_required=missing_required,
        all_required=all_required,
        all_acceptable=all_acceptable,
        relevant_rule_ids=relevant_rule_ids,
        critical_dimension_ids=critical_dimensions,
        target_platform=target_platform,
    )
    return RetrievalShadowUnitEvaluationV1(
        unit_id=truth_unit.unit_id,
        source_ref_id=truth_unit.source_ref_id,
        profile_id=truth_unit.profile_id,
        split=truth_unit.split,
        index_eligible_required_rule_ids=tuple(sorted(index_eligible_required)),
        missing_required_rule_ids=tuple(sorted(missing_required)),
        knowledge_gap=_ratio(len(missing_required), len(all_required)),
        runtime_formal_dimension_coverage=_ratio(
            len(formal_dimensions.intersection(critical_dimensions)),
            len(critical_dimensions),
        ),
        candidate_only_dimension_coverage=_ratio(
            len(candidate_only_dimensions.intersection(critical_dimensions)),
            len(critical_dimensions),
        ),
        arms=(static, hybrid),
        comparison=_unit_comparison(static, hybrid),
    )


class RetrievalShadowEvaluationBuilderV1:
    def build(
        self,
        item: RetrievalShadowEvaluationInputV1,
    ) -> RetrievalShadowEvaluationReportV1:
        if not isinstance(item, RetrievalShadowEvaluationInputV1):
            raise TypeError("evaluation input must use RetrievalShadowEvaluationInputV1")
        if type(item.truth) is not RetrievalDocumentTruthV1:
            raise TypeError("evaluation requires exact RetrievalDocumentTruthV1")
        if type(item.request) is not RetrievalRequestV3:
            raise TypeError("evaluation requires exact RetrievalRequestV3")
        if type(item.result) is not RetrievalShadowResultV3:
            raise TypeError("evaluation requires exact RetrievalShadowResultV3")
        if type(item.index) is not KnowledgeIndex:
            raise TypeError("evaluation requires exact KnowledgeIndex")
        truth = RetrievalDocumentTruthV1.model_validate(item.truth.model_dump(mode="json"))
        request = RetrievalRequestV3.model_validate(item.request.model_dump(mode="json"))
        result = RetrievalShadowResultV3.model_validate(item.result.model_dump(mode="json"))
        index = KnowledgeIndex.model_validate(item.index.model_dump(mode="json"))
        _verify_roots(truth, request, result, index)

        truth_by_unit = {unit.unit_id: unit for unit in truth.units}
        request_by_unit = {unit.unit_id: unit for unit in request.units}
        result_by_unit = {unit.unit_id: unit for unit in result.units}
        units = tuple(
            _build_unit_evaluation(
                truth_by_unit[unit_id],
                request_by_unit[unit_id],
                result_by_unit[unit_id],
                index,
                request.target_platform,
            )
            for unit_id in sorted(truth_by_unit)
        )
        static = _arm_aggregate("static_vector", units)
        hybrid = _arm_aggregate("hybrid", units)
        knowledge_gap, formal_coverage, candidate_coverage = _aggregate_unit_ratios(units)
        split_aggregates = tuple(
            _split_aggregate(
                split,
                tuple(item for item in units if item.split == split),
            )
            for split in DOCUMENT_TRUTH_SPLITS
            if any(item.split == split for item in units)
        )
        payload: dict[str, object] = {
            "schema_version": RETRIEVAL_SHADOW_EVALUATION_SCHEMA_VERSION,
            "truth_id": truth.truth_id,
            "request_id": request.request_id,
            "shadow_result_id": result.result_id,
            "index_version": index.index_version,
            "knowledge_build_id": index.published_build_id,
            "source_bundle_id": index.source_bundle_id,
            "base_retrieval_config_fingerprint": result.base_retrieval_config_fingerprint,
            "shadow_policy_fingerprint": result.shadow_policy_fingerprint,
            "unit_evaluations": units,
            "arm_aggregates": (static, hybrid),
            "arm_comparison": _aggregate_comparison(units),
            "split_aggregates": split_aggregates,
            "unit_count": len(units),
            "development_unit_count": sum(item.split == "development" for item in units),
            "calibration_unit_count": sum(item.split == "calibration" for item in units),
            "knowledge_gap": knowledge_gap,
            "runtime_formal_dimension_coverage": formal_coverage,
            "candidate_only_dimension_coverage": candidate_coverage,
            "metric_aggregation": "micro_counts_and_unit_mean_mrr",
            "evaluation_scope": "relative_gain_on_fixed_index",
            "verification_root_scope": (
                "caller_supplied_self_hashed_truth_request_result_and_index"
            ),
            "authority_status": "serialized_audit_only",
            "downstream_use": "offline_relative_evaluation_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
            "user_visible": False,
            "prompt_eligible": False,
            "finding_evidence_eligible": False,
            "qualification_blockers": _QUALIFICATION_BLOCKERS,
        }
        try:
            validated = _RetrievalShadowEvaluationPayloadV1.model_validate(payload)
            sealed = validated.model_dump(mode="json")
            sealed["report_id"] = _canonical_hash("retrieval-shadow-evaluation", sealed)
            return RetrievalShadowEvaluationReportV1.model_validate(sealed)
        except ValidationError as exc:
            raise ValueError(f"invalid Retrieval Shadow Evaluation payload: {exc}") from exc

    def verify(
        self,
        report: RetrievalShadowEvaluationReportV1,
        item: RetrievalShadowEvaluationInputV1,
    ) -> None:
        if type(report) is not RetrievalShadowEvaluationReportV1:
            raise TypeError("evaluation verifier requires exact report type")
        canonical = RetrievalShadowEvaluationReportV1.model_validate(report.model_dump(mode="json"))
        if canonical != self.build(item):
            raise ValueError("Retrieval Shadow Evaluation does not rebuild from supplied roots")


def build_retrieval_shadow_evaluation_v1(
    item: RetrievalShadowEvaluationInputV1,
) -> RetrievalShadowEvaluationReportV1:
    return RetrievalShadowEvaluationBuilderV1().build(item)


def verify_retrieval_shadow_evaluation_v1(
    report: RetrievalShadowEvaluationReportV1,
    item: RetrievalShadowEvaluationInputV1,
) -> None:
    RetrievalShadowEvaluationBuilderV1().verify(report, item)


def load_retrieval_shadow_evaluation_v1(
    raw: str | bytes,
) -> RetrievalShadowEvaluationReportV1:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Retrieval Shadow Evaluation must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Retrieval Shadow Evaluation input must be str or bytes")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, _DuplicateKeyError, ValueError) as exc:
        raise ValueError(f"invalid Retrieval Shadow Evaluation JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "invalid Retrieval Shadow Evaluation JSON: top-level value must be an object"
        )
    try:
        return RetrievalShadowEvaluationReportV1.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval Shadow Evaluation: {exc}") from exc


__all__ = [
    "METRIC_K_VALUES",
    "RETRIEVAL_SHADOW_EVALUATION_SCHEMA_VERSION",
    "AggregateArmComparisonV1",
    "AggregateRankingEvaluationV1",
    "ArmAggregateV1",
    "ArmUnitEvaluationV1",
    "MeanMetricV1",
    "MetricDeltaAtKV1",
    "RankingEvaluationV1",
    "RankingMetricsAtKV1",
    "RatioMetricV1",
    "RetrievalShadowEvaluationBuilderV1",
    "RetrievalShadowEvaluationInputV1",
    "RetrievalShadowEvaluationReportV1",
    "RetrievalShadowUnitEvaluationV1",
    "SplitAggregateV1",
    "UnitArmComparisonV1",
    "build_retrieval_shadow_evaluation_v1",
    "load_retrieval_shadow_evaluation_v1",
    "verify_retrieval_shadow_evaluation_v1",
]
