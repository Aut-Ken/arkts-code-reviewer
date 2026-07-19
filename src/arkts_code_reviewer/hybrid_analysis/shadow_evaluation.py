from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import (
    AITagDispatchEnvelopeBuilder,
    VerifiedAITagDispatchEnvelope,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
    ResponseValidationReason,
    ResponseValidationStatus,
    verify_ai_tag_response_validation,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    ACTIVE_TAG_COUNT_V1,
    AITagUsage,
    ReviewUnitAnalysisCard,
    StaticDecision,
    TagDecision,
    UnitComparisonStatus,
    reduce_unit_comparison,
    verify_model_view_against_card,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import (
    FullTaxonomyRequestBuilder,
)

AI_TAG_SHADOW_UNIT_EVALUATION_SCHEMA_VERSION = "ai-tag-shadow-unit-evaluation-v1"
AI_TAG_SHADOW_EVALUATION_REPORT_SCHEMA_VERSION = "ai-tag-shadow-evaluation-report-v1"

ShadowResponseSourceKind = Literal[
    "scripted_fixture",
    "unverified_raw",
    "unverified_transport_claim",
]
ShadowQualificationBlocker = Literal[
    "analysis_card_upstream_provenance_not_rebuilt",
    "document_retrieval_truth_not_evaluated",
    "evaluation_campaign_manifest_not_bound",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "provider_attribution_not_formal",
    "shadow_validation_not_formal_ai_result",
]

_QUALIFICATION_BLOCKERS: tuple[ShadowQualificationBlocker, ...] = (
    "analysis_card_upstream_provenance_not_rebuilt",
    "document_retrieval_truth_not_evaluated",
    "evaluation_campaign_manifest_not_bound",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "provider_attribution_not_formal",
    "shadow_validation_not_formal_ai_result",
)

_HASH = r"[0-9a-f]{64}"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_VALIDATION_ID = rf"^ai-tag-response-validation:sha256:{_HASH}$"
_UNIT_EVALUATION_ID = rf"^ai-tag-shadow-unit-evaluation:sha256:{_HASH}$"
_REPORT_ID = rf"^ai-tag-shadow-evaluation-report:sha256:{_HASH}$"
_FEATURE_CONFIG_FINGERPRINT = rf"^feature-config:sha256:{_HASH}$"
_FEATURE_PROFILE_ID = rf"^feature-profile:sha256:{_HASH}$"
_FEATURE_ROUTING_ID = rf"^feature-routing:sha256:{_HASH}$"
_CONTEXT_POLICY_FINGERPRINT = rf"^analysis-context-policy:sha256:{_HASH}$"
_PROJECTION_POLICY_FINGERPRINT = rf"^ai-model-view-policy:sha256:{_HASH}$"
_TAXONOMY_FINGERPRINT = rf"^ai-tag-taxonomy:sha256:{_HASH}$"
_CATALOG_FINGERPRINT = rf"^ai-tag-contract-catalog:sha256:{_HASH}$"
_MODEL_POLICY_FINGERPRINT = rf"^ai-tag-policy:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"
_TAG_ID = r"^has_[a-z0-9_]+$"

_INVALID_REASONS: set[str] = {
    "evidence_out_of_range",
    "incomplete_taxonomy",
    "invalid_json",
    "non_stop_finish_reason",
    "response_empty",
    "schema_invalid",
}
_UNAVAILABLE_REASONS: set[str] = {
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
}


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _sorted_unique_strings(
    values: tuple[str, ...],
    context: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not allow_empty and not values:
        raise ValueError(f"{context} must not be empty")
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _optional_single_line(
    value: str | None,
    context: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"{context} must be non-empty, trimmed, single-line, and at most "
            f"{maximum} characters"
        )
    return value


def _revalidate[ModelT: FrozenModel](
    model: ModelT,
    model_type: type[ModelT],
    context: str,
) -> ModelT:
    try:
        return model_type.model_validate(model.model_dump(mode="json"))
    except (AttributeError, ValidationError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


class AITagShadowDecisionCounts(FrozenModel):
    positive: Annotated[int, Field(ge=0)]
    not_supported: Annotated[int, Field(ge=0)]
    abstain: Annotated[int, Field(ge=0)]
    validated_content_decision_absent: Annotated[int, Field(ge=0)]

    @property
    def total(self) -> int:
        return (
            self.positive
            + self.not_supported
            + self.abstain
            + self.validated_content_decision_absent
        )


class AITagShadowComparisonCounts(FrozenModel):
    agreement_positive: Annotated[int, Field(ge=0)]
    disagreement: Annotated[int, Field(ge=0)]
    static_only: Annotated[int, Field(ge=0)]
    ai_only: Annotated[int, Field(ge=0)]
    no_positive_signal: Annotated[int, Field(ge=0)]
    unresolved: Annotated[int, Field(ge=0)]
    static_only_due_execution: Annotated[int, Field(ge=0)]
    unresolved_due_execution: Annotated[int, Field(ge=0)]

    @property
    def total(self) -> int:
        return (
            self.agreement_positive
            + self.disagreement
            + self.static_only
            + self.ai_only
            + self.no_positive_signal
            + self.unresolved
            + self.static_only_due_execution
            + self.unresolved_due_execution
        )


class AITagShadowTagComparison(FrozenModel):
    """Diagnostic static-exact x validated-content state; never formal Hybrid state."""

    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    static_exact_decision: StaticDecision
    static_routing_decision: StaticDecision
    validated_content_decision: TagDecision | None
    unit_comparison_status: UnitComparisonStatus

    @model_validator(mode="after")
    def validate_comparison_status(self) -> Self:
        expected = reduce_unit_comparison(
            self.static_exact_decision,
            self.validated_content_decision,
        )
        if self.unit_comparison_status != expected:
            raise ValueError(
                "shadow Tag comparison status does not match static-exact/AI axes"
            )
        return self


def _decision_counts(
    comparisons: Sequence[AITagShadowTagComparison],
) -> AITagShadowDecisionCounts:
    decisions = tuple(item.validated_content_decision for item in comparisons)
    return AITagShadowDecisionCounts(
        positive=decisions.count("positive"),
        not_supported=decisions.count("not_supported"),
        abstain=decisions.count("abstain"),
        validated_content_decision_absent=decisions.count(None),
    )


def _comparison_counts(
    comparisons: Sequence[AITagShadowTagComparison],
) -> AITagShadowComparisonCounts:
    statuses = tuple(item.unit_comparison_status for item in comparisons)
    return AITagShadowComparisonCounts(
        agreement_positive=statuses.count("agreement_positive"),
        disagreement=statuses.count("disagreement"),
        static_only=statuses.count("static_only"),
        ai_only=statuses.count("ai_only"),
        no_positive_signal=statuses.count("no_positive_signal"),
        unresolved=statuses.count("unresolved"),
        static_only_due_execution=statuses.count("static_only_due_execution"),
        unresolved_due_execution=statuses.count("unresolved_due_execution"),
    )


class _AITagShadowUnitEvaluationPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-unit-evaluation-v1"]
    unit_id: Annotated[str, Field(min_length=1)]
    unit_kind: Literal[
        "struct",
        "class",
        "function",
        "method",
        "build_method",
        "builder",
        "ui_block",
        "field_region",
        "import_region",
        "fallback",
    ]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    feature_profile_id: Annotated[str, Field(pattern=_FEATURE_PROFILE_ID)]
    feature_routing_id: Annotated[str, Field(pattern=_FEATURE_ROUTING_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    response_validation_id: Annotated[str, Field(pattern=_VALIDATION_ID)]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=_FEATURE_CONFIG_FINGERPRINT),
    ]
    context_policy_fingerprint: Annotated[
        str,
        Field(pattern=_CONTEXT_POLICY_FINGERPRINT),
    ]
    projection_policy_fingerprint: Annotated[
        str,
        Field(pattern=_PROJECTION_POLICY_FINGERPRINT),
    ]
    active_taxonomy_fingerprint: Annotated[
        str,
        Field(pattern=_TAXONOMY_FINGERPRINT),
    ]
    catalog_fingerprint: Annotated[str, Field(pattern=_CATALOG_FINGERPRINT)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    model_policy_fingerprint: Annotated[str, Field(pattern=_MODEL_POLICY_FINGERPRINT)]
    response_source_kind: ShadowResponseSourceKind
    response_status: ResponseValidationStatus
    response_reason_code: ResponseValidationReason
    requested_model: Literal["deepseek-v4-pro"]
    validation_model: Annotated[str | None, Field(max_length=100)]
    validation_system_fingerprint: Annotated[str | None, Field(max_length=500)]
    validation_finish_reason: Annotated[str | None, Field(max_length=100)]
    raw_content_sha256: Annotated[str | None, Field(pattern=_SHA256)]
    tag_comparisons: tuple[AITagShadowTagComparison, ...]
    decision_counts: AITagShadowDecisionCounts
    comparison_counts: AITagShadowComparisonCounts
    reported_usage: AITagUsage
    reported_latency_ms: Annotated[int, Field(ge=0)]
    reported_attempt_count: Annotated[int, Field(ge=1)]
    verification_root_scope: Literal[
        "caller_supplied_sealed_card_envelope_and_response_validation"
    ]
    output_scope: Literal["diagnostic_only_no_hybrid_no_retrieval"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    qualification_blockers: tuple[ShadowQualificationBlocker, ...]

    @field_validator("tag_comparisons", "qualification_blockers", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AITagShadowUnitEvaluation.{info.field_name}")

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        if (
            not value
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("shadow Unit evaluation unit_id must be trimmed and single-line")
        return value

    @field_validator(
        "validation_model",
        "validation_system_fingerprint",
        "validation_finish_reason",
    )
    @classmethod
    def validate_optional_metadata(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        maximum = 500 if info.field_name == "validation_system_fingerprint" else 100
        return _optional_single_line(
            value,
            f"AITagShadowUnitEvaluation.{info.field_name}",
            maximum,
        )

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        tag_ids = tuple(item.tag_id for item in self.tag_comparisons)
        if len(tag_ids) != ACTIVE_TAG_COUNT_V1 or tag_ids != tuple(sorted(set(tag_ids))):
            raise ValueError(
                "shadow Unit evaluation must contain canonical unique 24-Tag comparisons"
            )
        if self.decision_counts != _decision_counts(self.tag_comparisons):
            raise ValueError("shadow Unit decision counts do not rebuild from comparisons")
        if self.comparison_counts != _comparison_counts(self.tag_comparisons):
            raise ValueError("shadow Unit comparison counts do not rebuild from comparisons")
        decisions_present = tuple(
            item.validated_content_decision is not None
            for item in self.tag_comparisons
        )
        if self.response_status == "valid_shape":
            if self.response_reason_code != "response_shape_valid" or not all(
                decisions_present
            ):
                raise ValueError(
                    "valid shadow Unit evaluation requires all 24 validated-content decisions"
                )
            if self.response_source_kind == "unverified_transport_claim":
                raise ValueError("transport failure claim cannot be a valid response")
            if (
                self.validation_model != self.requested_model
                or self.validation_system_fingerprint is None
                or self.validation_finish_reason != "stop"
                or self.raw_content_sha256 is None
            ):
                raise ValueError("valid shadow Unit evaluation metadata is inconsistent")
        elif self.response_status == "invalid_output":
            if self.response_reason_code not in _INVALID_REASONS or any(decisions_present):
                raise ValueError(
                    "invalid shadow Unit evaluation cannot carry validated-content decisions"
                )
            if self.response_source_kind == "unverified_transport_claim":
                raise ValueError("transport failure claim cannot be invalid output")
            if (
                self.validation_model is None
                or self.validation_system_fingerprint is None
                or self.validation_finish_reason is None
                or self.raw_content_sha256 is None
            ):
                raise ValueError("invalid shadow Unit evaluation metadata is incomplete")
        else:
            if self.response_reason_code not in _UNAVAILABLE_REASONS or any(
                decisions_present
            ):
                raise ValueError(
                    "unavailable shadow Unit evaluation cannot carry validated-content decisions"
                )
            if self.response_source_kind == "unverified_raw":
                raise ValueError("unverified raw completion cannot be unavailable claim")
            if any(
                value is not None
                for value in (
                    self.validation_model,
                    self.validation_system_fingerprint,
                    self.validation_finish_reason,
                    self.raw_content_sha256,
                )
            ):
                raise ValueError("unavailable shadow Unit evaluation metadata must be null")
            if self.reported_usage.input_tokens is not None:
                raise ValueError("unavailable shadow Unit evaluation must have null usage")
        if self.qualification_blockers != _QUALIFICATION_BLOCKERS:
            raise ValueError("shadow Unit evaluation must retain all qualification blockers")
        return self


class AITagShadowUnitEvaluation(_AITagShadowUnitEvaluationPayload):
    unit_evaluation_id: Annotated[str, Field(pattern=_UNIT_EVALUATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-unit-evaluation",
            identity_payload(self, "unit_evaluation_id"),
        )
        if self.unit_evaluation_id != expected:
            raise ValueError("shadow Unit evaluation ID does not match its contents")
        return self


class AITagShadowTagAggregate(FrozenModel):
    tag_id: Annotated[str, Field(pattern=_TAG_ID)]
    static_exact_positive_unit_count: Annotated[int, Field(ge=0)]
    static_routing_positive_unit_count: Annotated[int, Field(ge=0)]
    decision_counts: AITagShadowDecisionCounts
    comparison_counts: AITagShadowComparisonCounts


def _sum_decision_counts(
    counts: Sequence[AITagShadowDecisionCounts],
) -> AITagShadowDecisionCounts:
    return AITagShadowDecisionCounts(
        positive=sum(item.positive for item in counts),
        not_supported=sum(item.not_supported for item in counts),
        abstain=sum(item.abstain for item in counts),
        validated_content_decision_absent=sum(
            item.validated_content_decision_absent for item in counts
        ),
    )


def _sum_comparison_counts(
    counts: Sequence[AITagShadowComparisonCounts],
) -> AITagShadowComparisonCounts:
    return AITagShadowComparisonCounts(
        agreement_positive=sum(item.agreement_positive for item in counts),
        disagreement=sum(item.disagreement for item in counts),
        static_only=sum(item.static_only for item in counts),
        ai_only=sum(item.ai_only for item in counts),
        no_positive_signal=sum(item.no_positive_signal for item in counts),
        unresolved=sum(item.unresolved for item in counts),
        static_only_due_execution=sum(
            item.static_only_due_execution for item in counts
        ),
        unresolved_due_execution=sum(
            item.unresolved_due_execution for item in counts
        ),
    )


def _tag_aggregates(
    units: Sequence[AITagShadowUnitEvaluation],
) -> tuple[AITagShadowTagAggregate, ...]:
    if not units:
        raise ValueError("shadow Tag aggregation requires at least one Unit")
    tag_ids = tuple(item.tag_id for item in units[0].tag_comparisons)
    if any(
        tuple(item.tag_id for item in unit.tag_comparisons) != tag_ids
        for unit in units[1:]
    ):
        raise ValueError("shadow Unit evaluations must use the same canonical Tag set")
    states_by_unit = tuple(
        {item.tag_id: item for item in unit.tag_comparisons} for unit in units
    )
    aggregates: list[AITagShadowTagAggregate] = []
    for tag_id in tag_ids:
        comparisons = tuple(states[tag_id] for states in states_by_unit)
        aggregates.append(
            AITagShadowTagAggregate(
                tag_id=tag_id,
                static_exact_positive_unit_count=sum(
                    item.static_exact_decision == "positive" for item in comparisons
                ),
                static_routing_positive_unit_count=sum(
                    item.static_routing_decision == "positive" for item in comparisons
                ),
                decision_counts=_decision_counts(comparisons),
                comparison_counts=_comparison_counts(comparisons),
            )
        )
    return tuple(aggregates)


class _AITagShadowEvaluationReportPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-evaluation-report-v1"]
    feature_config_fingerprint: Annotated[
        str,
        Field(pattern=_FEATURE_CONFIG_FINGERPRINT),
    ]
    context_policy_fingerprint: Annotated[
        str,
        Field(pattern=_CONTEXT_POLICY_FINGERPRINT),
    ]
    projection_policy_fingerprint: Annotated[
        str,
        Field(pattern=_PROJECTION_POLICY_FINGERPRINT),
    ]
    active_taxonomy_fingerprint: Annotated[
        str,
        Field(pattern=_TAXONOMY_FINGERPRINT),
    ]
    catalog_fingerprint: Annotated[str, Field(pattern=_CATALOG_FINGERPRINT)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    model_policy_fingerprint: Annotated[str, Field(pattern=_MODEL_POLICY_FINGERPRINT)]
    unit_evaluations: tuple[AITagShadowUnitEvaluation, ...]
    unit_count: Annotated[int, Field(ge=1)]
    valid_shape_unit_count: Annotated[int, Field(ge=0)]
    invalid_output_unit_count: Annotated[int, Field(ge=0)]
    unavailable_claim_unit_count: Annotated[int, Field(ge=0)]
    scripted_fixture_unit_count: Annotated[int, Field(ge=0)]
    unverified_raw_unit_count: Annotated[int, Field(ge=0)]
    unverified_transport_claim_unit_count: Annotated[int, Field(ge=0)]
    valid_judgment_slot_count: Annotated[int, Field(ge=0)]
    usage_reported_unit_count: Annotated[int, Field(ge=0)]
    usage_unreported_unit_count: Annotated[int, Field(ge=0)]
    reported_input_tokens_total: Annotated[int, Field(ge=0)]
    reported_output_tokens_total: Annotated[int, Field(ge=0)]
    reported_cache_read_input_tokens_total: Annotated[int, Field(ge=0)]
    reported_latency_unit_count: Annotated[int, Field(ge=1)]
    reported_latency_total_ms: Annotated[int, Field(ge=0)]
    reported_latency_min_ms: Annotated[int, Field(ge=0)]
    reported_latency_max_ms: Annotated[int, Field(ge=0)]
    reported_attempt_count_total: Annotated[int, Field(ge=1)]
    decision_totals: AITagShadowDecisionCounts
    comparison_totals: AITagShadowComparisonCounts
    tag_aggregates: tuple[AITagShadowTagAggregate, ...]
    verification_root_scope: Literal[
        "caller_supplied_sealed_card_envelope_and_response_validation"
    ]
    collection_scope: Literal["caller_supplied_input_set_not_campaign_bound"]
    output_scope: Literal["distribution_only_no_truth_no_hybrid_no_retrieval"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    qualification_blockers: tuple[ShadowQualificationBlocker, ...]

    @field_validator(
        "unit_evaluations",
        "tag_aggregates",
        "qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"AITagShadowEvaluationReport.{info.field_name}")

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        order = tuple(
            (item.unit_id, item.card_id, item.unit_evaluation_id)
            for item in self.unit_evaluations
        )
        if len(order) != self.unit_count or order != tuple(sorted(set(order))):
            raise ValueError(
                "shadow report Unit evaluations must be sorted with no duplicate"
            )
        for attribute in (
            "unit_id",
            "card_id",
            "model_view_id",
            "request_id",
            "envelope_id",
            "response_validation_id",
            "unit_evaluation_id",
        ):
            values = tuple(getattr(item, attribute) for item in self.unit_evaluations)
            if len(values) != len(set(values)):
                raise ValueError(f"shadow report contains duplicate {attribute}")

        shared_fields = (
            "feature_config_fingerprint",
            "context_policy_fingerprint",
            "projection_policy_fingerprint",
            "active_taxonomy_fingerprint",
            "catalog_fingerprint",
            "prompt_hash",
            "model_policy_fingerprint",
        )
        for field_name in shared_fields:
            if any(
                getattr(item, field_name) != getattr(self, field_name)
                for item in self.unit_evaluations
            ):
                raise ValueError(
                    "shadow report mixes Unit evaluations with different "
                    f"{field_name}"
                )

        statuses = tuple(item.response_status for item in self.unit_evaluations)
        sources = tuple(item.response_source_kind for item in self.unit_evaluations)
        expected_status_counts = (
            statuses.count("valid_shape"),
            statuses.count("invalid_output"),
            statuses.count("unavailable_claim"),
        )
        if expected_status_counts != (
            self.valid_shape_unit_count,
            self.invalid_output_unit_count,
            self.unavailable_claim_unit_count,
        ):
            raise ValueError("shadow report response status counts do not rebuild")
        expected_source_counts = (
            sources.count("scripted_fixture"),
            sources.count("unverified_raw"),
            sources.count("unverified_transport_claim"),
        )
        if expected_source_counts != (
            self.scripted_fixture_unit_count,
            self.unverified_raw_unit_count,
            self.unverified_transport_claim_unit_count,
        ):
            raise ValueError("shadow report response source counts do not rebuild")
        if self.valid_judgment_slot_count != (
            self.valid_shape_unit_count * ACTIVE_TAG_COUNT_V1
        ):
            raise ValueError("shadow report valid judgment slot count does not rebuild")

        reported_usage = tuple(
            item
            for item in self.unit_evaluations
            if item.reported_usage.input_tokens is not None
        )
        if (
            self.usage_reported_unit_count != len(reported_usage)
            or self.usage_unreported_unit_count != self.unit_count - len(reported_usage)
        ):
            raise ValueError("shadow report usage reporting counts do not rebuild")
        if (
            self.reported_input_tokens_total
            != sum(item.reported_usage.input_tokens or 0 for item in reported_usage)
            or self.reported_output_tokens_total
            != sum(item.reported_usage.output_tokens or 0 for item in reported_usage)
            or self.reported_cache_read_input_tokens_total
            != sum(
                item.reported_usage.cache_read_input_tokens or 0
                for item in reported_usage
            )
        ):
            raise ValueError("shadow report token totals do not rebuild")

        latencies = tuple(item.reported_latency_ms for item in self.unit_evaluations)
        if (
            self.reported_latency_unit_count != self.unit_count
            or self.reported_latency_total_ms != sum(latencies)
            or self.reported_latency_min_ms != min(latencies)
            or self.reported_latency_max_ms != max(latencies)
        ):
            raise ValueError("shadow report latency metrics do not rebuild")
        if self.reported_attempt_count_total != sum(
            item.reported_attempt_count for item in self.unit_evaluations
        ):
            raise ValueError("shadow report reported attempt total does not rebuild")

        expected_decisions = _sum_decision_counts(
            tuple(item.decision_counts for item in self.unit_evaluations)
        )
        expected_comparisons = _sum_comparison_counts(
            tuple(item.comparison_counts for item in self.unit_evaluations)
        )
        if self.decision_totals != expected_decisions:
            raise ValueError("shadow report decision totals do not rebuild")
        if self.comparison_totals != expected_comparisons:
            raise ValueError("shadow report comparison totals do not rebuild")
        expected_tag_aggregates = _tag_aggregates(self.unit_evaluations)
        if self.tag_aggregates != expected_tag_aggregates:
            raise ValueError("shadow report per-Tag aggregates do not rebuild")
        if self.decision_totals.total != self.unit_count * ACTIVE_TAG_COUNT_V1:
            raise ValueError("shadow report decision denominator is inconsistent")
        if self.comparison_totals.total != self.unit_count * ACTIVE_TAG_COUNT_V1:
            raise ValueError("shadow report comparison denominator is inconsistent")
        if self.qualification_blockers != _QUALIFICATION_BLOCKERS:
            raise ValueError("shadow report must retain all qualification blockers")
        return self


class AITagShadowEvaluationReport(_AITagShadowEvaluationReportPayload):
    report_id: Annotated[str, Field(pattern=_REPORT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-evaluation-report",
            identity_payload(self, "report_id"),
        )
        if self.report_id != expected:
            raise ValueError("shadow evaluation report ID does not match its contents")
        return self


@dataclass(frozen=True)
class AITagShadowEvaluationInput:
    """Caller-supplied non-formal roots for one deterministic diagnostic record."""

    card: ReviewUnitAnalysisCard
    envelope: VerifiedAITagDispatchEnvelope
    response_validation: AITagResponseValidation


def seal_ai_tag_shadow_unit_evaluation(
    payload: Mapping[str, object],
) -> AITagShadowUnitEvaluation:
    return seal_payload(
        payload,
        payload_type=_AITagShadowUnitEvaluationPayload,
        sealed_type=AITagShadowUnitEvaluation,
        identity_field="unit_evaluation_id",
        identity_prefix="ai-tag-shadow-unit-evaluation",
        context="AI Tag Shadow Unit Evaluation",
    )


def seal_ai_tag_shadow_evaluation_report(
    payload: Mapping[str, object],
) -> AITagShadowEvaluationReport:
    return seal_payload(
        payload,
        payload_type=_AITagShadowEvaluationReportPayload,
        sealed_type=AITagShadowEvaluationReport,
        identity_field="report_id",
        identity_prefix="ai-tag-shadow-evaluation-report",
        context="AI Tag Shadow Evaluation Report",
    )


def load_ai_tag_shadow_unit_evaluation(raw: str | bytes) -> AITagShadowUnitEvaluation:
    return load_json_model(
        raw,
        AITagShadowUnitEvaluation,
        "AI Tag Shadow Unit Evaluation",
    )


def load_ai_tag_shadow_evaluation_report(raw: str | bytes) -> AITagShadowEvaluationReport:
    return load_json_model(
        raw,
        AITagShadowEvaluationReport,
        "AI Tag Shadow Evaluation Report",
    )


@dataclass(frozen=True)
class AITagShadowEvaluationBuilder:
    """Offline-only builder. It has no provider, credential, or Retrieval dependency."""

    request_builder: FullTaxonomyRequestBuilder

    @classmethod
    def default(cls) -> AITagShadowEvaluationBuilder:
        return cls(request_builder=FullTaxonomyRequestBuilder.default())

    def build_unit(
        self,
        item: AITagShadowEvaluationInput,
    ) -> AITagShadowUnitEvaluation:
        if not isinstance(item, AITagShadowEvaluationInput):
            raise TypeError("shadow evaluation item must be AITagShadowEvaluationInput")
        card = _revalidate(item.card, ReviewUnitAnalysisCard, "Analysis Card")
        envelope = _revalidate(
            item.envelope,
            VerifiedAITagDispatchEnvelope,
            "Verified AI Tag Dispatch Envelope",
        )
        validation = _revalidate(
            item.response_validation,
            AITagResponseValidation,
            "AI Tag Response Validation",
        )

        verify_model_view_against_card(envelope.model_view, card)
        expected_envelope = AITagDispatchEnvelopeBuilder(
            request_builder=self.request_builder
        ).build(
            card=card,
            model_view=envelope.model_view,
            request=envelope.analysis_request,
        )
        if envelope != expected_envelope:
            raise ValueError("shadow evaluation envelope differs from trusted rebuild")
        verify_ai_tag_response_validation(validation, envelope)

        tag_ids = tuple(
            contract.tag_id for contract in envelope.analysis_request.tag_contract_views
        )
        if not set((*card.static_tags.exact, *card.static_tags.routing)).issubset(tag_ids):
            raise ValueError("Analysis Card static Tags are outside the requested taxonomy")
        decisions = (
            {item.tag_id: item.decision for item in validation.judgments}
            if validation.status == "valid_shape"
            else {}
        )
        comparisons = tuple(
            AITagShadowTagComparison(
                tag_id=tag_id,
                static_exact_decision=(
                    "positive" if tag_id in card.static_tags.exact else "unknown"
                ),
                static_routing_decision=(
                    "positive" if tag_id in card.static_tags.routing else "unknown"
                ),
                validated_content_decision=decisions.get(tag_id),
                unit_comparison_status=reduce_unit_comparison(
                    "positive" if tag_id in card.static_tags.exact else "unknown",
                    decisions.get(tag_id),
                ),
            )
            for tag_id in tag_ids
        )
        return seal_ai_tag_shadow_unit_evaluation(
            {
                "schema_version": AI_TAG_SHADOW_UNIT_EVALUATION_SCHEMA_VERSION,
                "unit_id": card.unit_id,
                "unit_kind": card.unit_kind,
                "card_id": card.card_id,
                "feature_profile_id": card.feature_profile_id,
                "feature_routing_id": card.feature_routing_id,
                "model_view_id": envelope.model_view.model_view_id,
                "request_id": envelope.analysis_request.request_id,
                "envelope_id": envelope.envelope_id,
                "response_validation_id": validation.validation_id,
                "feature_config_fingerprint": card.feature_config_fingerprint,
                "context_policy_fingerprint": card.context_policy_fingerprint,
                "projection_policy_fingerprint": (
                    envelope.model_view.projection_policy_fingerprint
                ),
                "active_taxonomy_fingerprint": (
                    envelope.analysis_request.active_taxonomy_fingerprint
                ),
                "catalog_fingerprint": self.request_builder.catalog.catalog_fingerprint,
                "prompt_hash": envelope.analysis_request.prompt_hash,
                "model_policy_fingerprint": (
                    envelope.analysis_request.model_policy_fingerprint
                ),
                "response_source_kind": validation.source_kind,
                "response_status": validation.status,
                "response_reason_code": validation.reason_code,
                "requested_model": envelope.model_policy.model,
                "validation_model": validation.model,
                "validation_system_fingerprint": validation.system_fingerprint,
                "validation_finish_reason": validation.finish_reason,
                "raw_content_sha256": validation.raw_content_sha256,
                "tag_comparisons": comparisons,
                "decision_counts": _decision_counts(comparisons),
                "comparison_counts": _comparison_counts(comparisons),
                "reported_usage": validation.usage,
                "reported_latency_ms": validation.latency_ms,
                "reported_attempt_count": validation.attempt_count,
                "verification_root_scope": (
                    "caller_supplied_sealed_card_envelope_and_response_validation"
                ),
                "output_scope": "diagnostic_only_no_hybrid_no_retrieval",
                "evidence_qualification_status": "not_qualified",
                "production_qualified": False,
                "qualification_blockers": _QUALIFICATION_BLOCKERS,
            }
        )

    def build_report(
        self,
        items: Sequence[AITagShadowEvaluationInput],
    ) -> AITagShadowEvaluationReport:
        if not items:
            raise ValueError("shadow evaluation report requires at least one Unit")
        units = tuple(
            sorted(
                (self.build_unit(item) for item in items),
                key=lambda item: (item.unit_id, item.card_id, item.unit_evaluation_id),
            )
        )
        first = units[0]
        statuses = tuple(item.response_status for item in units)
        sources = tuple(item.response_source_kind for item in units)
        reported_usage = tuple(
            item for item in units if item.reported_usage.input_tokens is not None
        )
        latencies = tuple(item.reported_latency_ms for item in units)
        return seal_ai_tag_shadow_evaluation_report(
            {
                "schema_version": AI_TAG_SHADOW_EVALUATION_REPORT_SCHEMA_VERSION,
                "feature_config_fingerprint": first.feature_config_fingerprint,
                "context_policy_fingerprint": first.context_policy_fingerprint,
                "projection_policy_fingerprint": first.projection_policy_fingerprint,
                "active_taxonomy_fingerprint": first.active_taxonomy_fingerprint,
                "catalog_fingerprint": first.catalog_fingerprint,
                "prompt_hash": first.prompt_hash,
                "model_policy_fingerprint": first.model_policy_fingerprint,
                "unit_evaluations": units,
                "unit_count": len(units),
                "valid_shape_unit_count": statuses.count("valid_shape"),
                "invalid_output_unit_count": statuses.count("invalid_output"),
                "unavailable_claim_unit_count": statuses.count("unavailable_claim"),
                "scripted_fixture_unit_count": sources.count("scripted_fixture"),
                "unverified_raw_unit_count": sources.count("unverified_raw"),
                "unverified_transport_claim_unit_count": sources.count(
                    "unverified_transport_claim"
                ),
                "valid_judgment_slot_count": (
                    statuses.count("valid_shape") * ACTIVE_TAG_COUNT_V1
                ),
                "usage_reported_unit_count": len(reported_usage),
                "usage_unreported_unit_count": len(units) - len(reported_usage),
                "reported_input_tokens_total": sum(
                    item.reported_usage.input_tokens or 0 for item in reported_usage
                ),
                "reported_output_tokens_total": sum(
                    item.reported_usage.output_tokens or 0 for item in reported_usage
                ),
                "reported_cache_read_input_tokens_total": sum(
                    item.reported_usage.cache_read_input_tokens or 0
                    for item in reported_usage
                ),
                "reported_latency_unit_count": len(units),
                "reported_latency_total_ms": sum(latencies),
                "reported_latency_min_ms": min(latencies),
                "reported_latency_max_ms": max(latencies),
                "reported_attempt_count_total": sum(
                    item.reported_attempt_count for item in units
                ),
                "decision_totals": _sum_decision_counts(
                    tuple(item.decision_counts for item in units)
                ),
                "comparison_totals": _sum_comparison_counts(
                    tuple(item.comparison_counts for item in units)
                ),
                "tag_aggregates": _tag_aggregates(units),
                "verification_root_scope": (
                    "caller_supplied_sealed_card_envelope_and_response_validation"
                ),
                "collection_scope": "caller_supplied_input_set_not_campaign_bound",
                "output_scope": "distribution_only_no_truth_no_hybrid_no_retrieval",
                "evidence_qualification_status": "not_qualified",
                "production_qualified": False,
                "qualification_blockers": _QUALIFICATION_BLOCKERS,
            }
        )

    def verify_unit(
        self,
        unit: AITagShadowUnitEvaluation,
        item: AITagShadowEvaluationInput,
    ) -> None:
        canonical = _revalidate(
            unit,
            AITagShadowUnitEvaluation,
            "AI Tag Shadow Unit Evaluation",
        )
        if canonical != self.build_unit(item):
            raise ValueError("shadow Unit evaluation does not rebuild from supplied roots")

    def verify_report(
        self,
        report: AITagShadowEvaluationReport,
        items: Sequence[AITagShadowEvaluationInput],
    ) -> None:
        canonical = _revalidate(
            report,
            AITagShadowEvaluationReport,
            "AI Tag Shadow Evaluation Report",
        )
        if canonical != self.build_report(items):
            raise ValueError("shadow evaluation report does not rebuild from supplied roots")


def build_ai_tag_shadow_unit_evaluation(
    item: AITagShadowEvaluationInput,
) -> AITagShadowUnitEvaluation:
    return AITagShadowEvaluationBuilder.default().build_unit(item)


def build_ai_tag_shadow_evaluation_report(
    items: Sequence[AITagShadowEvaluationInput],
) -> AITagShadowEvaluationReport:
    return AITagShadowEvaluationBuilder.default().build_report(items)


def verify_ai_tag_shadow_unit_evaluation(
    unit: AITagShadowUnitEvaluation,
    item: AITagShadowEvaluationInput,
) -> None:
    AITagShadowEvaluationBuilder.default().verify_unit(unit, item)


def verify_ai_tag_shadow_evaluation_report(
    report: AITagShadowEvaluationReport,
    items: Sequence[AITagShadowEvaluationInput],
) -> None:
    AITagShadowEvaluationBuilder.default().verify_report(report, items)


__all__ = [
    "AI_TAG_SHADOW_EVALUATION_REPORT_SCHEMA_VERSION",
    "AI_TAG_SHADOW_UNIT_EVALUATION_SCHEMA_VERSION",
    "AITagShadowComparisonCounts",
    "AITagShadowDecisionCounts",
    "AITagShadowEvaluationBuilder",
    "AITagShadowEvaluationInput",
    "AITagShadowEvaluationReport",
    "AITagShadowTagAggregate",
    "AITagShadowTagComparison",
    "AITagShadowUnitEvaluation",
    "ShadowQualificationBlocker",
    "ShadowResponseSourceKind",
    "build_ai_tag_shadow_evaluation_report",
    "build_ai_tag_shadow_unit_evaluation",
    "load_ai_tag_shadow_evaluation_report",
    "load_ai_tag_shadow_unit_evaluation",
    "seal_ai_tag_shadow_evaluation_report",
    "seal_ai_tag_shadow_unit_evaluation",
    "verify_ai_tag_shadow_evaluation_report",
    "verify_ai_tag_shadow_unit_evaluation",
]
