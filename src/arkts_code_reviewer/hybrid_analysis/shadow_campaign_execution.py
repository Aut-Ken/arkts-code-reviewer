from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import ContextPlanResult
from arkts_code_reviewer.code_analysis.models import AnalysisResult
from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    identity_payload,
    load_json_model,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.builders import (
    AIModelViewProjectionPolicy,
    AnalysisContextPolicy,
    AnalysisParserProfile,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekOuterResponseDiagnostic,
    DeepSeekShadowHttpTransport,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
    verify_ai_tag_response_validation,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagDispatchAttemptReceipt,
    AITagObservedProviderResponseReceiptV2,
    AITagShadowDispatchClaims,
    AITagShadowExecutionObservationV2,
    verify_ai_tag_dispatch_attempt_receipt,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    DEFAULT_CAMPAIGN_PROJECTION_POLICY,
    DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY,
    AITagShadowCampaignBuilder,
    AITagShadowCampaignBundle,
    AITagShadowCampaignUnitArtifacts,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowRunArtifacts,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
    preflight_deepseek_shadow_live_transport,
    verify_deepseek_shadow_run_artifacts,
)

AI_TAG_SHADOW_CAMPAIGN_UNIT_EXECUTION_SCHEMA_VERSION: Literal[
    "ai-tag-shadow-campaign-unit-execution-v1"
] = "ai-tag-shadow-campaign-unit-execution-v1"
AI_TAG_SHADOW_CAMPAIGN_EXECUTION_RESULT_SCHEMA_VERSION: Literal[
    "ai-tag-shadow-campaign-execution-result-v1"
] = "ai-tag-shadow-campaign-execution-result-v1"
AI_TAG_SHADOW_CAMPAIGN_NON_ATTEMPT_RECEIPT_SCHEMA_VERSION: Literal[
    "ai-tag-shadow-campaign-non-attempt-receipt-v1"
] = "ai-tag-shadow-campaign-non-attempt-receipt-v1"

_HASH = r"[0-9a-f]{64}"
_CAMPAIGN_ID = rf"^ai-tag-shadow-campaign:sha256:{_HASH}$"
_UNIT_EXECUTION_ID = rf"^ai-tag-shadow-campaign-unit-execution:sha256:{_HASH}$"
_EXECUTION_RESULT_ID = rf"^ai-tag-shadow-campaign-execution-result:sha256:{_HASH}$"
_NON_ATTEMPT_RECEIPT_ID = rf"^ai-tag-shadow-campaign-non-attempt:sha256:{_HASH}$"
_SOURCE_REF_ID = rf"^code-source:sha256:{_HASH}$"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_CLAIMS_ID = rf"^ai-tag-shadow-claims:sha256:{_HASH}$"
_ATTEMPT_RECEIPT_ID = rf"^ai-tag-attempt-receipt:sha256:{_HASH}$"
_RESPONSE_RECEIPT_ID = rf"^ai-tag-observed-response:sha256:{_HASH}$"
_VALIDATION_ID = rf"^ai-tag-response-validation:sha256:{_HASH}$"
_OUTER_DIAGNOSTIC_ID = rf"^deepseek-outer-response-diagnostic:sha256:{_HASH}$"
_OBSERVATION_ID = rf"^ai-tag-shadow-observation:sha256:{_HASH}$"
_SHA256 = rf"^sha256:{_HASH}$"

DispatchDisposition = Literal["attempted", "skipped_budget", "not_run"]
AttemptOutcome = Literal[
    "valid_shape",
    "invalid_output_inner",
    "invalid_output_outer",
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
    "provider_transport_error",
    "provider_response_too_large",
]
LocalNonAttemptReason = Literal[
    "budget_not_reserved",
    "egress_not_approved",
    "credential_not_configured",
    "campaign_wall_clock_budget_insufficient",
]
NonAttemptControlStage = Literal[
    "egress_authorization",
    "credential_availability",
    "budget_reservation",
    "campaign_deadline_preflight",
    "campaign_deadline_post_authorization",
]
AttemptTransportStatus = Literal[
    "response_received",
    "provider_timeout",
    "provider_transport_error",
    "provider_response_too_large",
]
ObservationStatus = Literal[
    "valid_shape",
    "invalid_output",
    "provider_client_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_timeout",
    "provider_transport_error",
    "provider_response_too_large",
]
CampaignExecutionQualificationBlocker = Literal[
    "document_retrieval_truth_not_evaluated",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "source_git_provenance_not_attested",
    "external_authority_attestation_missing",
    "provider_signature_missing",
    "trusted_runner_attestation_missing",
]

_QUALIFICATION_BLOCKERS: tuple[CampaignExecutionQualificationBlocker, ...] = (
    "document_retrieval_truth_not_evaluated",
    "independent_tag_truth_missing",
    "production_prevalence_not_measured",
    "source_git_provenance_not_attested",
    "external_authority_attestation_missing",
    "provider_signature_missing",
    "trusted_runner_attestation_missing",
)

_PROVIDER_FAILURE_OUTCOMES: frozenset[str] = frozenset(
    (
        "provider_client_error",
        "provider_rate_limited",
        "provider_server_error",
        "provider_timeout",
        "provider_transport_error",
        "provider_response_too_large",
    )
)
_INNER_INVALID_REASONS: frozenset[str] = frozenset(
    (
        "evidence_out_of_range",
        "incomplete_taxonomy",
        "invalid_json",
        "non_stop_finish_reason",
        "response_empty",
        "schema_invalid",
    )
)
_TRANSPORT_STATUS_BY_OUTCOME: dict[AttemptOutcome, AttemptTransportStatus] = {
    "valid_shape": "response_received",
    "invalid_output_inner": "response_received",
    "invalid_output_outer": "response_received",
    "provider_client_error": "response_received",
    "provider_rate_limited": "response_received",
    "provider_server_error": "response_received",
    "provider_timeout": "provider_timeout",
    "provider_transport_error": "provider_transport_error",
    "provider_response_too_large": "provider_response_too_large",
}


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


class AITagShadowCampaignExecutionLimits(FrozenModel):
    max_units: Annotated[int, Field(ge=1, le=64)]
    max_total_wire_body_bytes: Annotated[int, Field(ge=1, le=64_000_000)]
    max_total_output_tokens: Annotated[int, Field(ge=256, le=262_144)]
    max_total_response_bytes: Annotated[int, Field(ge=1_024, le=128_000_000)]
    campaign_wall_clock_cap_ms: Annotated[int, Field(ge=1_000, le=3_600_000)]


class _AITagShadowCampaignNonAttemptReceiptPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-campaign-non-attempt-receipt-v1"]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    dispatch_disposition: Literal["skipped_budget", "not_run"]
    local_non_attempt_reason: LocalNonAttemptReason
    observed_control_stage: NonAttemptControlStage
    campaign_elapsed_ms: Annotated[int | None, Field(ge=0, le=86_400_000)]
    attempt_count: Literal[0]
    control_evidence_scope: Literal["process_local_observation_not_external_authority"]

    @model_validator(mode="after")
    def validate_non_attempt_matrix(self) -> Self:
        expected: dict[
            Literal[
                "budget_not_reserved",
                "egress_not_approved",
                "credential_not_configured",
            ],
            tuple[Literal["skipped_budget", "not_run"], NonAttemptControlStage],
        ] = {
            "budget_not_reserved": ("skipped_budget", "budget_reservation"),
            "egress_not_approved": ("not_run", "egress_authorization"),
            "credential_not_configured": ("not_run", "credential_availability"),
        }
        if self.local_non_attempt_reason == "campaign_wall_clock_budget_insufficient":
            if (
                self.dispatch_disposition != "not_run"
                or self.observed_control_stage
                not in {
                    "campaign_deadline_preflight",
                    "campaign_deadline_post_authorization",
                }
                or self.campaign_elapsed_ms is None
            ):
                raise ValueError("Campaign deadline observation requires elapsed time")
        else:
            if (
                self.dispatch_disposition,
                self.observed_control_stage,
            ) != expected[self.local_non_attempt_reason]:
                raise ValueError("non-attempt disposition, reason, and control stage differ")
            if self.campaign_elapsed_ms is not None:
                raise ValueError("only Campaign deadline observations retain elapsed time")
        return self


class AITagShadowCampaignNonAttemptReceipt(_AITagShadowCampaignNonAttemptReceiptPayload):
    receipt_id: Annotated[str, Field(pattern=_NON_ATTEMPT_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-campaign-non-attempt",
            identity_payload(self, "receipt_id"),
        )
        if self.receipt_id != expected:
            raise ValueError("Campaign non-attempt receipt ID does not match its contents")
        return self


class _AITagShadowCampaignUnitExecutionPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-campaign-unit-execution-v1"]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    unit_id: Annotated[str, Field(min_length=1, max_length=2_000)]
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
    source_ref_id: Annotated[str, Field(pattern=_SOURCE_REF_ID)]
    source_role: Literal["base", "head"]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    claims_id: Annotated[str, Field(pattern=_CLAIMS_ID)]
    dispatch_disposition: DispatchDisposition
    attempt_outcome: AttemptOutcome | None
    local_non_attempt_reason: LocalNonAttemptReason | None
    non_attempt_receipt_id: Annotated[
        str | None,
        Field(pattern=_NON_ATTEMPT_RECEIPT_ID),
    ]
    attempt_receipt_id: Annotated[str | None, Field(pattern=_ATTEMPT_RECEIPT_ID)]
    provider_response_receipt_id: Annotated[
        str | None,
        Field(pattern=_RESPONSE_RECEIPT_ID),
    ]
    response_validation_id: Annotated[str | None, Field(pattern=_VALIDATION_ID)]
    outer_diagnostic_id: Annotated[str | None, Field(pattern=_OUTER_DIAGNOSTIC_ID)]
    observation_id: Annotated[str | None, Field(pattern=_OBSERVATION_ID)]
    attempt_transport_status: AttemptTransportStatus | None
    transport_evidence: (
        Literal[
            "httpx_tls_fixed_endpoint",
            "injected_untrusted_transport",
        ]
        | None
    )
    network_observation: (
        Literal[
            "observed_by_fixed_httpx_transport",
            "not_established_by_injected_transport",
        ]
        | None
    )
    observation_status: ObservationStatus | None
    observation_reason_code: Annotated[str | None, Field(min_length=1, max_length=100)]
    attempt_count: Annotated[int, Field(ge=0, le=1)]
    local_control_scope: Literal["self_reported_process_local_gate_not_external_authority"]
    execution_evidence_scope: Literal["unattested_shadow_not_formal"]

    @field_validator("unit_id")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("campaign Unit execution unit_id must be trimmed and single-line")
        return value

    @field_validator("observation_reason_code")
    @classmethod
    def validate_reason_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("observation reason must be lowercase alphanumeric snake case")
        return value

    @model_validator(mode="after")
    def validate_execution_matrix(self) -> Self:
        attempt_refs = (
            self.attempt_receipt_id,
            self.observation_id,
            self.attempt_transport_status,
            self.transport_evidence,
            self.network_observation,
            self.observation_status,
            self.observation_reason_code,
        )
        response_refs = (
            self.provider_response_receipt_id,
            self.response_validation_id,
            self.outer_diagnostic_id,
        )
        if self.dispatch_disposition != "attempted":
            if self.attempt_outcome is not None or any(value is not None for value in attempt_refs):
                raise ValueError("zero-attempt campaign Unit cannot carry attempt artifacts")
            if any(value is not None for value in response_refs) or self.attempt_count != 0:
                raise ValueError("zero-attempt campaign Unit cannot carry response artifacts")
            if self.non_attempt_receipt_id is None:
                raise ValueError("zero-attempt campaign Unit requires a local observation receipt")
            if self.dispatch_disposition == "skipped_budget":
                if self.local_non_attempt_reason != "budget_not_reserved":
                    raise ValueError("skipped-budget Unit requires local budget denial")
            elif self.local_non_attempt_reason not in {
                "egress_not_approved",
                "credential_not_configured",
                "campaign_wall_clock_budget_insufficient",
            }:
                raise ValueError("not-run Unit uses an unsupported local reason")
            return self

        if self.attempt_outcome is None or self.local_non_attempt_reason is not None:
            raise ValueError("attempted Unit requires an outcome and no non-attempt reason")
        if self.non_attempt_receipt_id is not None:
            raise ValueError("attempted Unit cannot carry a non-attempt receipt")
        if any(value is None for value in attempt_refs) or self.attempt_count != 1:
            raise ValueError("attempted Unit requires one complete attempt observation")
        expected_network_observation = (
            "observed_by_fixed_httpx_transport"
            if self.transport_evidence == "httpx_tls_fixed_endpoint"
            else "not_established_by_injected_transport"
        )
        if self.network_observation != expected_network_observation:
            raise ValueError("Campaign Unit transport and network evidence labels differ")
        expected_transport_status = _TRANSPORT_STATUS_BY_OUTCOME[self.attempt_outcome]
        if self.attempt_transport_status != expected_transport_status:
            raise ValueError("Campaign Unit outcome and transport status differ")
        if self.attempt_outcome == "valid_shape":
            if (
                self.observation_status != "valid_shape"
                or self.observation_reason_code != "response_shape_valid"
                or self.provider_response_receipt_id is None
                or self.response_validation_id is None
                or self.outer_diagnostic_id is not None
            ):
                raise ValueError("valid campaign Unit uses an invalid artifact matrix")
        elif self.attempt_outcome == "invalid_output_inner":
            if (
                self.observation_status != "invalid_output"
                or self.observation_reason_code not in _INNER_INVALID_REASONS
                or self.provider_response_receipt_id is None
                or self.response_validation_id is None
                or self.outer_diagnostic_id is not None
            ):
                raise ValueError("inner-invalid campaign Unit uses an invalid artifact matrix")
        elif self.attempt_outcome == "invalid_output_outer":
            if (
                self.observation_status != "invalid_output"
                or self.observation_reason_code != "provider_outer_contract_invalid"
                or self.provider_response_receipt_id is not None
                or self.response_validation_id is not None
                or self.outer_diagnostic_id is None
            ):
                raise ValueError("outer-invalid campaign Unit uses an invalid artifact matrix")
        elif self.attempt_outcome in _PROVIDER_FAILURE_OUTCOMES:
            if (
                self.observation_status != self.attempt_outcome
                or self.observation_reason_code != self.attempt_outcome
                or any(value is not None for value in response_refs)
            ):
                raise ValueError("provider-failure campaign Unit uses an invalid artifact matrix")
        return self


class AITagShadowCampaignUnitExecution(_AITagShadowCampaignUnitExecutionPayload):
    unit_execution_id: Annotated[str, Field(pattern=_UNIT_EXECUTION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-campaign-unit-execution",
            identity_payload(self, "unit_execution_id"),
        )
        if self.unit_execution_id != expected:
            raise ValueError("campaign Unit execution ID does not match its contents")
        return self


class AITagShadowCampaignExecutionCounts(FrozenModel):
    planned_unit_count: Annotated[int, Field(ge=1)]
    attempted_unit_count: Annotated[int, Field(ge=0)]
    skipped_budget_unit_count: Annotated[int, Field(ge=0)]
    not_run_unit_count: Annotated[int, Field(ge=0)]
    valid_shape_count: Annotated[int, Field(ge=0)]
    inner_invalid_count: Annotated[int, Field(ge=0)]
    outer_invalid_count: Annotated[int, Field(ge=0)]
    provider_client_error_count: Annotated[int, Field(ge=0)]
    provider_rate_limited_count: Annotated[int, Field(ge=0)]
    provider_server_error_count: Annotated[int, Field(ge=0)]
    provider_timeout_count: Annotated[int, Field(ge=0)]
    provider_transport_error_count: Annotated[int, Field(ge=0)]
    provider_response_too_large_count: Annotated[int, Field(ge=0)]
    fixed_httpx_attempt_count: Annotated[int, Field(ge=0)]
    injected_transport_attempt_count: Annotated[int, Field(ge=0)]
    network_observed_attempt_count: Annotated[int, Field(ge=0)]
    non_attempt_receipt_count: Annotated[int, Field(ge=0)]
    attempt_receipt_count: Annotated[int, Field(ge=0)]
    provider_response_receipt_count: Annotated[int, Field(ge=0)]
    response_validation_count: Annotated[int, Field(ge=0)]
    outer_diagnostic_count: Annotated[int, Field(ge=0)]


def _execution_counts(
    units: tuple[AITagShadowCampaignUnitExecution, ...],
) -> AITagShadowCampaignExecutionCounts:
    def outcomes(value: str) -> int:
        return sum(item.attempt_outcome == value for item in units)

    return AITagShadowCampaignExecutionCounts(
        planned_unit_count=len(units),
        attempted_unit_count=sum(item.dispatch_disposition == "attempted" for item in units),
        skipped_budget_unit_count=sum(
            item.dispatch_disposition == "skipped_budget" for item in units
        ),
        not_run_unit_count=sum(item.dispatch_disposition == "not_run" for item in units),
        valid_shape_count=outcomes("valid_shape"),
        inner_invalid_count=outcomes("invalid_output_inner"),
        outer_invalid_count=outcomes("invalid_output_outer"),
        provider_client_error_count=outcomes("provider_client_error"),
        provider_rate_limited_count=outcomes("provider_rate_limited"),
        provider_server_error_count=outcomes("provider_server_error"),
        provider_timeout_count=outcomes("provider_timeout"),
        provider_transport_error_count=outcomes("provider_transport_error"),
        provider_response_too_large_count=outcomes("provider_response_too_large"),
        fixed_httpx_attempt_count=sum(
            item.transport_evidence == "httpx_tls_fixed_endpoint" for item in units
        ),
        injected_transport_attempt_count=sum(
            item.transport_evidence == "injected_untrusted_transport" for item in units
        ),
        network_observed_attempt_count=sum(
            item.network_observation == "observed_by_fixed_httpx_transport" for item in units
        ),
        non_attempt_receipt_count=sum(item.non_attempt_receipt_id is not None for item in units),
        attempt_receipt_count=sum(item.attempt_receipt_id is not None for item in units),
        provider_response_receipt_count=sum(
            item.provider_response_receipt_id is not None for item in units
        ),
        response_validation_count=sum(item.response_validation_id is not None for item in units),
        outer_diagnostic_count=sum(item.outer_diagnostic_id is not None for item in units),
    )


class _AITagShadowCampaignExecutionResultPayload(FrozenModel):
    schema_version: Literal["ai-tag-shadow-campaign-execution-result-v1"]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    execution_policy_version: Literal["canonical_order_per_plan_single_attempt_no_retry_v1"]
    execution_limits: AITagShadowCampaignExecutionLimits
    units: tuple[AITagShadowCampaignUnitExecution, ...]
    counts: AITagShadowCampaignExecutionCounts
    execution_scope: Literal["shadow_only_no_hybrid_no_retrieval"]
    verification_root_scope: Literal[
        "campaign_upstream_graph_runtime_artifacts_local_non_attempt_receipts_"
        "and_optional_raw_response_bytes"
    ]
    raw_response_rebuild_scope: Literal[
        "caller_supplied_raw_bytes_required_for_offline_full_rebuild"
    ]
    event_identity_scope: Literal["content_identity_not_unique_occurrence_attestation"]
    local_control_scope: Literal["process_local_gate_observation_not_external_authority"]
    provider_evidence_scope: Literal["local_runtime_observation_not_provider_signature"]
    source_provenance_scope: Literal["content_hash_replayed_git_attestation_not_verified"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    qualification_blockers: tuple[CampaignExecutionQualificationBlocker, ...]

    @field_validator("units", "qualification_blockers", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: ValidationInfo) -> tuple[object, ...]:
        return _sequence(value, f"campaign Execution Result {info.field_name}")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        order = tuple((item.unit_id, item.card_id, item.plan_id) for item in self.units)
        if not order or order != tuple(sorted(set(order))):
            raise ValueError("campaign execution Units must be canonical and unique")
        for attribute in (
            "unit_execution_id",
            "unit_id",
            "card_id",
            "model_view_id",
            "request_id",
            "envelope_id",
            "plan_id",
        ):
            values = tuple(getattr(item, attribute) for item in self.units)
            if len(values) != len(set(values)):
                raise ValueError(f"campaign execution contains duplicate {attribute}")
        if any(item.campaign_id != self.campaign_id for item in self.units):
            raise ValueError("campaign Unit execution refers to a different Campaign")
        if self.counts != _execution_counts(self.units):
            raise ValueError("campaign execution counts do not rebuild from Units")
        if self.qualification_blockers != _QUALIFICATION_BLOCKERS:
            raise ValueError("campaign execution must retain every qualification blocker")
        return self


class AITagShadowCampaignExecutionResult(_AITagShadowCampaignExecutionResultPayload):
    execution_result_id: Annotated[str, Field(pattern=_EXECUTION_RESULT_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-campaign-execution-result",
            identity_payload(self, "execution_result_id"),
        )
        if self.execution_result_id != expected:
            raise ValueError("campaign execution Result ID does not match its contents")
        return self


def seal_ai_tag_shadow_campaign_unit_execution(
    payload: Mapping[str, object],
) -> AITagShadowCampaignUnitExecution:
    return seal_payload(
        payload,
        payload_type=_AITagShadowCampaignUnitExecutionPayload,
        sealed_type=AITagShadowCampaignUnitExecution,
        identity_field="unit_execution_id",
        identity_prefix="ai-tag-shadow-campaign-unit-execution",
        context="AI Tag Shadow Campaign Unit Execution",
    )


def seal_ai_tag_shadow_campaign_non_attempt_receipt(
    payload: Mapping[str, object],
) -> AITagShadowCampaignNonAttemptReceipt:
    return seal_payload(
        payload,
        payload_type=_AITagShadowCampaignNonAttemptReceiptPayload,
        sealed_type=AITagShadowCampaignNonAttemptReceipt,
        identity_field="receipt_id",
        identity_prefix="ai-tag-shadow-campaign-non-attempt",
        context="AI Tag Shadow Campaign Non-Attempt Receipt",
    )


def load_ai_tag_shadow_campaign_non_attempt_receipt(
    raw: str | bytes,
) -> AITagShadowCampaignNonAttemptReceipt:
    return load_json_model(
        raw,
        AITagShadowCampaignNonAttemptReceipt,
        "AI Tag Shadow Campaign Non-Attempt Receipt",
    )


def load_ai_tag_shadow_campaign_unit_execution(
    raw: str | bytes,
) -> AITagShadowCampaignUnitExecution:
    return load_json_model(
        raw,
        AITagShadowCampaignUnitExecution,
        "AI Tag Shadow Campaign Unit Execution",
    )


def seal_ai_tag_shadow_campaign_execution_result(
    payload: Mapping[str, object],
) -> AITagShadowCampaignExecutionResult:
    return seal_payload(
        payload,
        payload_type=_AITagShadowCampaignExecutionResultPayload,
        sealed_type=AITagShadowCampaignExecutionResult,
        identity_field="execution_result_id",
        identity_prefix="ai-tag-shadow-campaign-execution-result",
        context="AI Tag Shadow Campaign Execution Result",
    )


def load_ai_tag_shadow_campaign_execution_result(
    raw: str | bytes,
) -> AITagShadowCampaignExecutionResult:
    return load_json_model(
        raw,
        AITagShadowCampaignExecutionResult,
        "AI Tag Shadow Campaign Execution Result",
    )


@dataclass(frozen=True, repr=False)
class AITagShadowCampaignTrustedUpstream:
    bundle: AITagShadowCampaignBundle
    analysis_result: AnalysisResult
    context_plan: ContextPlanResult
    source_snapshots: Mapping[str, CodeSourceSnapshot]
    unit_ids: tuple[str, ...]
    context_policy: AnalysisContextPolicy = DEFAULT_PROVIDER_EGRESS_ANALYSIS_CONTEXT_POLICY
    projection_policy: AIModelViewProjectionPolicy = DEFAULT_CAMPAIGN_PROJECTION_POLICY
    max_output_tokens: int = 4_096
    timeout_ms: int = 60_000
    max_response_bytes: int = 2_000_000
    parser_profile: AnalysisParserProfile = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, AITagShadowCampaignBundle):
            raise TypeError("trusted Campaign upstream requires a Campaign Bundle")
        if not isinstance(self.unit_ids, tuple) or not self.unit_ids:
            raise ValueError("trusted Campaign upstream requires explicit Unit IDs")
        self.verify()

    def __repr__(self) -> str:
        return "AITagShadowCampaignTrustedUpstream(<caller-owned-upstream-roots>)"

    def verify(self) -> None:
        AITagShadowCampaignBuilder.default().verify_against_upstream(
            self.bundle,
            analysis_result=self.analysis_result,
            context_plan=self.context_plan,
            source_snapshots=self.source_snapshots,
            unit_ids=self.unit_ids,
            context_policy=self.context_policy,
            projection_policy=self.projection_policy,
            max_output_tokens=self.max_output_tokens,
            timeout_ms=self.timeout_ms,
            max_response_bytes=self.max_response_bytes,
            parser_profile=self.parser_profile,
        )


@dataclass(frozen=True, repr=False)
class AITagShadowCampaignRuntimeBinding:
    claims: AITagShadowDispatchClaims
    gate: AITagShadowAuthorizationGate
    transport: DeepSeekShadowHttpTransport | None = None

    def __post_init__(self) -> None:
        claims = AITagShadowDispatchClaims.model_validate(self.claims.model_dump(mode="json"))
        if not isinstance(self.gate, AITagShadowAuthorizationGate):
            raise TypeError("Campaign runtime binding requires an Authorization Gate")
        if self.transport is not None and not callable(getattr(self.transport, "send", None)):
            raise TypeError("Campaign runtime transport does not implement send")
        object.__setattr__(self, "claims", claims)

    def __repr__(self) -> str:
        return "AITagShadowCampaignRuntimeBinding(<runtime-only-controls>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("Campaign runtime bindings are not serializable")


@dataclass(frozen=True, repr=False)
class AITagShadowCampaignUnitEvidence:
    plan_id: str
    claims: AITagShadowDispatchClaims
    run_artifacts: AITagShadowRunArtifacts | None
    non_attempt_receipt: AITagShadowCampaignNonAttemptReceipt | None

    def __post_init__(self) -> None:
        if self.claims.plan_id != self.plan_id:
            raise ValueError("campaign Unit evidence Claims refer to a different Plan")
        if (self.run_artifacts is None) == (self.non_attempt_receipt is None):
            raise ValueError(
                "campaign Unit evidence requires exactly one attempt or non-attempt artifact"
            )
        if self.non_attempt_receipt is not None and (
            self.non_attempt_receipt.plan_id != self.plan_id
            or self.non_attempt_receipt.claims_id != self.claims.claims_id
        ):
            raise ValueError("campaign non-attempt receipt differs from Plan or Claims")

    def __repr__(self) -> str:
        return f"AITagShadowCampaignUnitEvidence(plan_id={self.plan_id!r}, <redacted>)"


@dataclass(frozen=True, repr=False)
class AITagShadowCampaignExecutionBundle:
    result: AITagShadowCampaignExecutionResult
    unit_evidence: tuple[AITagShadowCampaignUnitEvidence, ...]

    def __post_init__(self) -> None:
        result = AITagShadowCampaignExecutionResult.model_validate(
            self.result.model_dump(mode="json")
        )
        if not isinstance(self.unit_evidence, tuple):
            raise TypeError("campaign execution evidence must use a tuple")
        plan_ids = tuple(item.plan_id for item in self.unit_evidence)
        expected_plan_ids = tuple(item.plan_id for item in result.units)
        if plan_ids != expected_plan_ids or len(plan_ids) != len(set(plan_ids)):
            raise ValueError("campaign execution evidence must follow Result Unit order")
        object.__setattr__(self, "result", result)

    def __repr__(self) -> str:
        return (
            "AITagShadowCampaignExecutionBundle("
            f"execution_result_id={self.result.execution_result_id!r}, "
            f"unit_count={len(self.unit_evidence)})"
        )


def _common_unit_payload(
    *,
    campaign_id: str,
    unit: AITagShadowCampaignUnitArtifacts,
    claims: AITagShadowDispatchClaims,
) -> dict[str, object]:
    return {
        "schema_version": AI_TAG_SHADOW_CAMPAIGN_UNIT_EXECUTION_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "unit_id": unit.card.unit_id,
        "unit_kind": unit.card.unit_kind,
        "source_ref_id": unit.card.source_ref_id,
        "source_role": unit.card.source_role,
        "card_id": unit.card.card_id,
        "model_view_id": unit.model_view.model_view_id,
        "request_id": unit.request.request_id,
        "envelope_id": unit.envelope.envelope_id,
        "plan_id": unit.plan.plan_id,
        "wire_body_sha256": unit.plan.wire_body_sha256,
        "claims_id": claims.claims_id,
        "local_control_scope": ("self_reported_process_local_gate_not_external_authority"),
        "execution_evidence_scope": "unattested_shadow_not_formal",
    }


def _non_attempt_receipt(
    *,
    campaign_id: str,
    plan_id: str,
    claims_id: str,
    disposition: Literal["skipped_budget", "not_run"],
    reason: LocalNonAttemptReason,
    observed_control_stage: NonAttemptControlStage,
    campaign_elapsed_ms: int | None = None,
) -> AITagShadowCampaignNonAttemptReceipt:
    return seal_ai_tag_shadow_campaign_non_attempt_receipt(
        {
            "schema_version": (AI_TAG_SHADOW_CAMPAIGN_NON_ATTEMPT_RECEIPT_SCHEMA_VERSION),
            "campaign_id": campaign_id,
            "plan_id": plan_id,
            "claims_id": claims_id,
            "dispatch_disposition": disposition,
            "local_non_attempt_reason": reason,
            "observed_control_stage": observed_control_stage,
            "campaign_elapsed_ms": campaign_elapsed_ms,
            "attempt_count": 0,
            "control_evidence_scope": ("process_local_observation_not_external_authority"),
        }
    )


def _non_attempt_unit_execution(
    *,
    campaign_id: str,
    unit: AITagShadowCampaignUnitArtifacts,
    claims: AITagShadowDispatchClaims,
    receipt: AITagShadowCampaignNonAttemptReceipt,
) -> AITagShadowCampaignUnitExecution:
    if (
        receipt.campaign_id != campaign_id
        or receipt.plan_id != unit.plan.plan_id
        or receipt.claims_id != claims.claims_id
    ):
        raise ValueError("Campaign non-attempt receipt differs from its execution graph")
    payload = _common_unit_payload(campaign_id=campaign_id, unit=unit, claims=claims)
    payload.update(
        {
            "dispatch_disposition": receipt.dispatch_disposition,
            "attempt_outcome": None,
            "local_non_attempt_reason": receipt.local_non_attempt_reason,
            "non_attempt_receipt_id": receipt.receipt_id,
            "attempt_receipt_id": None,
            "provider_response_receipt_id": None,
            "response_validation_id": None,
            "outer_diagnostic_id": None,
            "observation_id": None,
            "attempt_transport_status": None,
            "transport_evidence": None,
            "network_observation": None,
            "observation_status": None,
            "observation_reason_code": None,
            "attempt_count": 0,
        }
    )
    return seal_ai_tag_shadow_campaign_unit_execution(payload)


def _attempt_outcome(artifacts: AITagShadowRunArtifacts) -> AttemptOutcome:
    observation = artifacts.observation
    if observation.status == "invalid_output":
        return (
            "invalid_output_outer"
            if artifacts.outer_response_diagnostic is not None
            else "invalid_output_inner"
        )
    return observation.status


def _attempted_unit_execution(
    *,
    campaign_id: str,
    unit: AITagShadowCampaignUnitArtifacts,
    claims: AITagShadowDispatchClaims,
    artifacts: AITagShadowRunArtifacts,
) -> AITagShadowCampaignUnitExecution:
    attempt = artifacts.attempt_receipt
    observation = artifacts.observation
    payload = _common_unit_payload(campaign_id=campaign_id, unit=unit, claims=claims)
    payload.update(
        {
            "dispatch_disposition": "attempted",
            "attempt_outcome": _attempt_outcome(artifacts),
            "local_non_attempt_reason": None,
            "non_attempt_receipt_id": None,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": observation.provider_response_receipt_id,
            "response_validation_id": observation.response_validation_id,
            "outer_diagnostic_id": observation.outer_diagnostic_id,
            "observation_id": observation.observation_id,
            "attempt_transport_status": attempt.transport_status,
            "transport_evidence": attempt.transport_evidence,
            "network_observation": attempt.network_observation,
            "observation_status": observation.status,
            "observation_reason_code": observation.reason_code,
            "attempt_count": 1,
        }
    )
    return seal_ai_tag_shadow_campaign_unit_execution(payload)


def _seal_execution_result(
    *,
    campaign_id: str,
    units: tuple[AITagShadowCampaignUnitExecution, ...],
    limits: AITagShadowCampaignExecutionLimits,
) -> AITagShadowCampaignExecutionResult:
    return seal_ai_tag_shadow_campaign_execution_result(
        {
            "schema_version": AI_TAG_SHADOW_CAMPAIGN_EXECUTION_RESULT_SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "execution_policy_version": ("canonical_order_per_plan_single_attempt_no_retry_v1"),
            "execution_limits": limits.model_dump(mode="json"),
            "units": tuple(item.model_dump(mode="json") for item in units),
            "counts": _execution_counts(units).model_dump(mode="json"),
            "execution_scope": "shadow_only_no_hybrid_no_retrieval",
            "verification_root_scope": (
                "campaign_upstream_graph_runtime_artifacts_local_non_attempt_receipts_"
                "and_optional_raw_response_bytes"
            ),
            "raw_response_rebuild_scope": (
                "caller_supplied_raw_bytes_required_for_offline_full_rebuild"
            ),
            "event_identity_scope": ("content_identity_not_unique_occurrence_attestation"),
            "local_control_scope": ("process_local_gate_observation_not_external_authority"),
            "provider_evidence_scope": ("local_runtime_observation_not_provider_signature"),
            "source_provenance_scope": ("content_hash_replayed_git_attestation_not_verified"),
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
            "qualification_blockers": _QUALIFICATION_BLOCKERS,
        }
    )


def _verify_execution_limits(
    campaign: AITagShadowCampaignBundle,
    limits: AITagShadowCampaignExecutionLimits,
) -> None:
    units = campaign.units
    total_body_bytes = sum(len(item.plan.wire_body_json.encode("utf-8")) for item in units)
    total_output_tokens = sum(item.plan.wire_payload.max_tokens for item in units)
    total_response_bytes = sum(item.plan.max_response_bytes for item in units)
    if len(units) > limits.max_units:
        raise ValueError("Campaign exceeds the explicit Unit cap")
    if total_body_bytes > limits.max_total_wire_body_bytes:
        raise ValueError("Campaign exceeds the explicit outbound body byte cap")
    if total_output_tokens > limits.max_total_output_tokens:
        raise ValueError("Campaign exceeds the explicit output token cap")
    if total_response_bytes > limits.max_total_response_bytes:
        raise ValueError("Campaign exceeds the explicit response byte cap")


def _verify_persistent_run_graph(
    *,
    unit: AITagShadowCampaignUnitArtifacts,
    claims: AITagShadowDispatchClaims,
    artifacts: AITagShadowRunArtifacts,
) -> None:
    attempt = AITagDispatchAttemptReceipt.model_validate(
        artifacts.attempt_receipt.model_dump(mode="json")
    )
    observation = AITagShadowExecutionObservationV2.model_validate(
        artifacts.observation.model_dump(mode="json")
    )
    verify_ai_tag_dispatch_attempt_receipt(attempt, plan=unit.plan, claims=claims)
    if (
        observation.plan_id != unit.plan.plan_id
        or observation.claims_id != claims.claims_id
        or observation.attempt_receipt_id != attempt.receipt_id
    ):
        raise ValueError("Campaign Observation differs from Plan, Claims, or Attempt")

    response_receipt = artifacts.provider_response_receipt
    validation = artifacts.response_validation
    diagnostic = artifacts.outer_response_diagnostic
    if observation.provider_response_receipt_id != (
        None if response_receipt is None else response_receipt.receipt_id
    ):
        raise ValueError("Campaign Observation differs from Response Receipt")
    if observation.response_validation_id != (
        None if validation is None else validation.validation_id
    ):
        raise ValueError("Campaign Observation differs from Response Validation")
    if observation.outer_diagnostic_id != (
        None if diagnostic is None else diagnostic.diagnostic_id
    ):
        raise ValueError("Campaign Observation differs from outer diagnostic")

    if attempt.transport_status != "response_received":
        if response_receipt is not None or validation is not None or diagnostic is not None:
            raise ValueError("Campaign transport failure cannot carry response artifacts")
        if (
            observation.status != attempt.transport_status
            or observation.reason_code != attempt.transport_status
        ):
            raise ValueError("Campaign transport failure differs from its Observation")
        return

    if attempt.http_status is None:
        raise ValueError("Campaign response-received Attempt lacks an HTTP status")
    if attempt.http_status != 200:
        expected_status: Literal[
            "provider_client_error",
            "provider_rate_limited",
            "provider_server_error",
        ]
        if attempt.http_status == 429:
            expected_status = "provider_rate_limited"
        elif 500 <= attempt.http_status <= 599:
            expected_status = "provider_server_error"
        else:
            expected_status = "provider_client_error"
        if response_receipt is not None or validation is not None or diagnostic is not None:
            raise ValueError("Campaign non-200 Attempt cannot carry parsed response artifacts")
        if observation.status != expected_status or observation.reason_code != expected_status:
            raise ValueError("Campaign HTTP failure differs from its Observation")
        return

    if response_receipt is None:
        if validation is not None or diagnostic is None:
            raise ValueError("Campaign outer-invalid Attempt uses an invalid artifact matrix")
        diagnostic = DeepSeekOuterResponseDiagnostic.model_validate(
            diagnostic.model_dump(mode="json")
        )
        if (
            diagnostic.plan_id != unit.plan.plan_id
            or diagnostic.response_body_sha256 != attempt.response_body_sha256
            or diagnostic.response_body_size_bytes != attempt.response_body_size_bytes
            or observation.status != "invalid_output"
            or observation.reason_code != "provider_outer_contract_invalid"
        ):
            raise ValueError("Campaign outer diagnostic differs from its HTTP 200 Attempt")
        return

    if validation is None or diagnostic is not None:
        raise ValueError("Campaign parsed response uses an invalid artifact matrix")
    response_receipt = AITagObservedProviderResponseReceiptV2.model_validate(
        response_receipt.model_dump(mode="json")
    )
    validation = AITagResponseValidation.model_validate(validation.model_dump(mode="json"))
    verify_ai_tag_response_validation(validation, unit.envelope)
    if (
        response_receipt.plan_id != unit.plan.plan_id
        or response_receipt.attempt_receipt_id != attempt.receipt_id
        or response_receipt.http_status != attempt.http_status
        or response_receipt.response_body_sha256 != attempt.response_body_sha256
        or response_receipt.response_body_size_bytes != attempt.response_body_size_bytes
        or response_receipt.transport_evidence != attempt.transport_evidence
    ):
        raise ValueError("Campaign Response Receipt differs from its HTTP 200 Attempt")
    raw_usage = response_receipt.usage
    expected_usage: tuple[int | None, int | None, int | None] = (None, None, None)
    if raw_usage is not None:
        candidate_usage = (
            raw_usage.prompt_tokens,
            raw_usage.completion_tokens,
            raw_usage.prompt_cache_hit_tokens,
        )
        if all(value is not None for value in candidate_usage):
            expected_usage = candidate_usage
    if (
        validation.source_kind != "unverified_raw"
        or validation.raw_content_sha256 != response_receipt.content_sha256
        or validation.model != response_receipt.model
        or validation.system_fingerprint != (response_receipt.system_fingerprint or "not_reported")
        or validation.finish_reason != response_receipt.finish_reason
        or validation.latency_ms != attempt.latency_ms
        or validation.attempt_count != 1
        or validation.usage.input_tokens != expected_usage[0]
        or validation.usage.output_tokens != expected_usage[1]
        or validation.usage.cache_read_input_tokens != expected_usage[2]
        or observation.status != validation.status
        or observation.reason_code != validation.reason_code
    ):
        raise ValueError("Campaign Response Validation differs from response metadata")


def verify_ai_tag_shadow_campaign_execution_result(
    result: AITagShadowCampaignExecutionResult,
    *,
    trusted_upstream: AITagShadowCampaignTrustedUpstream,
    expected_limits: AITagShadowCampaignExecutionLimits,
    evidence_by_plan_id: Mapping[str, AITagShadowCampaignUnitEvidence],
    raw_response_body_by_plan_id: Mapping[str, bytes] | None = None,
) -> None:
    """Verify a Result against caller-owned roots and optionally rebuild responses.

    ``expected_limits`` is deliberately supplied by the verifier caller.  The
    limits embedded in a self-sealed Result are evidence, not authority: using
    them as their own verification root would allow a Result to widen or shrink
    its approved execution budget and then simply recompute its content hash.
    """

    if not isinstance(trusted_upstream, AITagShadowCampaignTrustedUpstream):
        raise TypeError("campaign execution verifier requires trusted upstream roots")
    if not isinstance(expected_limits, AITagShadowCampaignExecutionLimits):
        raise TypeError("campaign execution verifier requires expected execution limits")
    trusted_upstream.verify()
    canonical = AITagShadowCampaignExecutionResult.model_validate(result.model_dump(mode="json"))
    campaign = trusted_upstream.bundle
    if canonical.campaign_id != campaign.manifest.campaign_id:
        raise ValueError("Campaign Execution Result refers to a different Manifest")
    if canonical.execution_limits != expected_limits:
        raise ValueError("Campaign Execution Result differs from expected execution limits")
    _verify_execution_limits(campaign, expected_limits)

    expected_plan_ids = tuple(item.plan.plan_id for item in campaign.units)
    if set(evidence_by_plan_id) != set(expected_plan_ids):
        raise ValueError("Campaign execution evidence does not exactly cover Manifest Plans")
    if len(canonical.units) != len(campaign.units):
        raise ValueError("Campaign Execution Result does not exactly cover Manifest Units")

    expected_raw_plan_ids = {
        item.plan_id
        for item in canonical.units
        if item.attempt_transport_status == "response_received"
    }
    if raw_response_body_by_plan_id is not None:
        if set(raw_response_body_by_plan_id) != expected_raw_plan_ids:
            raise ValueError("raw response mapping does not exactly cover response-received Plans")
        if any(not isinstance(value, bytes) for value in raw_response_body_by_plan_id.values()):
            raise TypeError("raw response mapping values must use bytes")

    rebuilt_units: list[AITagShadowCampaignUnitExecution] = []
    for unit, recorded in zip(campaign.units, canonical.units, strict=True):
        expected_identity = (
            unit.card.unit_id,
            unit.card.unit_kind,
            unit.card.source_ref_id,
            unit.card.source_role,
            unit.card.card_id,
            unit.model_view.model_view_id,
            unit.request.request_id,
            unit.envelope.envelope_id,
            unit.plan.plan_id,
            unit.plan.wire_body_sha256,
        )
        actual_identity = (
            recorded.unit_id,
            recorded.unit_kind,
            recorded.source_ref_id,
            recorded.source_role,
            recorded.card_id,
            recorded.model_view_id,
            recorded.request_id,
            recorded.envelope_id,
            recorded.plan_id,
            recorded.wire_body_sha256,
        )
        if actual_identity != expected_identity:
            raise ValueError("Campaign Unit execution differs from its Manifest Plan graph")
        evidence = evidence_by_plan_id[unit.plan.plan_id]
        if not isinstance(evidence, AITagShadowCampaignUnitEvidence):
            raise TypeError("campaign execution evidence mapping contains an invalid value")
        claims = AITagShadowDispatchClaims.model_validate(evidence.claims.model_dump(mode="json"))
        if recorded.claims_id != claims.claims_id or claims.plan_id != unit.plan.plan_id:
            raise ValueError("Campaign Unit execution differs from its Claims")
        if recorded.dispatch_disposition == "attempted":
            if unit.plan.wall_clock_timeout_ms > expected_limits.campaign_wall_clock_cap_ms:
                raise ValueError(
                    "attempted Campaign Unit Plan timeout exceeds expected wall-clock cap"
                )
            if evidence.run_artifacts is None:
                raise ValueError("attempted Campaign Unit lacks Runner artifacts")
            if evidence.non_attempt_receipt is not None:
                raise ValueError("attempted Campaign Unit carries a non-attempt receipt")
            _verify_persistent_run_graph(unit=unit, claims=claims, artifacts=evidence.run_artifacts)
            rebuilt = _attempted_unit_execution(
                campaign_id=campaign.manifest.campaign_id,
                unit=unit,
                claims=claims,
                artifacts=evidence.run_artifacts,
            )
            if raw_response_body_by_plan_id is not None:
                trusted_plan_inputs = AITagShadowTrustedPlanInputs(
                    envelope=unit.envelope,
                    card=unit.card,
                    context_policy=trusted_upstream.context_policy,
                    max_output_tokens=unit.plan.wire_payload.max_tokens,
                    wall_clock_timeout_ms=unit.plan.wall_clock_timeout_ms,
                    max_response_bytes=unit.plan.max_response_bytes,
                )
                verify_deepseek_shadow_run_artifacts(
                    evidence.run_artifacts,
                    plan=unit.plan,
                    claims=claims,
                    trusted_plan_inputs=trusted_plan_inputs,
                    raw_response_body=(
                        raw_response_body_by_plan_id[unit.plan.plan_id]
                        if unit.plan.plan_id in expected_raw_plan_ids
                        else None
                    ),
                )
        else:
            if evidence.run_artifacts is not None:
                raise ValueError("zero-attempt Campaign Unit cannot carry Runner artifacts")
            if evidence.non_attempt_receipt is None:
                raise ValueError("zero-attempt Campaign Unit lacks a local observation receipt")
            receipt = AITagShadowCampaignNonAttemptReceipt.model_validate(
                evidence.non_attempt_receipt.model_dump(mode="json")
            )
            if (
                receipt.campaign_id != campaign.manifest.campaign_id
                or receipt.plan_id != unit.plan.plan_id
                or receipt.claims_id != claims.claims_id
            ):
                raise ValueError("Campaign non-attempt receipt differs from its graph")
            if receipt.local_non_attempt_reason == ("campaign_wall_clock_budget_insufficient") and (
                receipt.campaign_elapsed_ms is None
                or receipt.campaign_elapsed_ms + unit.plan.wall_clock_timeout_ms
                <= expected_limits.campaign_wall_clock_cap_ms
            ):
                raise ValueError("Campaign deadline receipt does not prove insufficient time")
            rebuilt = _non_attempt_unit_execution(
                campaign_id=campaign.manifest.campaign_id,
                unit=unit,
                claims=claims,
                receipt=receipt,
            )
        if rebuilt != recorded:
            raise ValueError("Campaign Unit execution differs from its artifact rebuild")
        rebuilt_units.append(rebuilt)

    expected_result = _seal_execution_result(
        campaign_id=campaign.manifest.campaign_id,
        units=tuple(rebuilt_units),
        limits=expected_limits,
    )
    if canonical != expected_result:
        raise ValueError("Campaign Execution Result differs from its full graph rebuild")


class AITagShadowCampaignLiveHarness:
    """Canonical sequential multi-Unit shadow executor with no retry or batch authority."""

    def execute(
        self,
        *,
        trusted_upstream: AITagShadowCampaignTrustedUpstream,
        runtime_bindings_by_plan_id: Mapping[str, AITagShadowCampaignRuntimeBinding],
        limits: AITagShadowCampaignExecutionLimits,
        allow_live_transport: bool = False,
        allow_injected_transport: bool = False,
    ) -> AITagShadowCampaignExecutionBundle:
        if not isinstance(trusted_upstream, AITagShadowCampaignTrustedUpstream):
            raise TypeError("Campaign Harness requires trusted upstream roots")
        if not isinstance(limits, AITagShadowCampaignExecutionLimits):
            raise TypeError("Campaign Harness requires explicit execution limits")
        if type(allow_live_transport) is not bool:
            raise TypeError("allow_live_transport must be a bool")
        if type(allow_injected_transport) is not bool:
            raise TypeError("allow_injected_transport must be a bool")
        trusted_upstream.verify()
        campaign = trusted_upstream.bundle
        _verify_execution_limits(campaign, limits)

        expected_plan_ids = tuple(item.plan.plan_id for item in campaign.units)
        if set(runtime_bindings_by_plan_id) != set(expected_plan_ids):
            raise ValueError("Campaign runtime bindings must exactly cover Manifest Plans")
        bindings: dict[str, AITagShadowCampaignRuntimeBinding] = {}
        for unit in campaign.units:
            binding = runtime_bindings_by_plan_id[unit.plan.plan_id]
            if not isinstance(binding, AITagShadowCampaignRuntimeBinding):
                raise TypeError("Campaign runtime binding mapping contains an invalid value")
            if binding.claims.plan_id != unit.plan.plan_id:
                raise AITagShadowAuthorizationError("claims_mismatch")
            if binding.claims.trust_domain_id != binding.gate.trust_domain_id:
                raise AITagShadowAuthorizationError("claims_mismatch")
            try:
                binding.gate.verify_claims_binding(
                    plan=unit.plan,
                    claims=binding.claims,
                )
            except AITagShadowAuthorizationError:
                raise
            except ValueError:
                raise AITagShadowAuthorizationError("plan_not_trusted") from None
            uses_repository_live_transport = binding.transport is None
            if uses_repository_live_transport and not allow_live_transport:
                raise ValueError(
                    "real Campaign transport requires explicit allow_live_transport=True"
                )
            if not uses_repository_live_transport and not allow_injected_transport:
                raise ValueError(
                    "injected Campaign transport requires explicit allow_injected_transport=True"
                )
            bindings[unit.plan.plan_id] = binding

        if any(binding.transport is None for binding in bindings.values()):
            preflight_deepseek_shadow_live_transport()

        started_ns = time.monotonic_ns()
        records: list[AITagShadowCampaignUnitExecution] = []
        evidence_rows: list[AITagShadowCampaignUnitEvidence] = []
        for unit in campaign.units:
            binding = bindings[unit.plan.plan_id]
            elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
            remaining_ms = limits.campaign_wall_clock_cap_ms - elapsed_ms
            if remaining_ms < unit.plan.wall_clock_timeout_ms:
                receipt = _non_attempt_receipt(
                    campaign_id=campaign.manifest.campaign_id,
                    plan_id=unit.plan.plan_id,
                    claims_id=binding.claims.claims_id,
                    disposition="not_run",
                    reason="campaign_wall_clock_budget_insufficient",
                    observed_control_stage="campaign_deadline_preflight",
                    campaign_elapsed_ms=elapsed_ms,
                )
                record = _non_attempt_unit_execution(
                    campaign_id=campaign.manifest.campaign_id,
                    unit=unit,
                    claims=binding.claims,
                    receipt=receipt,
                )
                records.append(record)
                evidence_rows.append(
                    AITagShadowCampaignUnitEvidence(
                        plan_id=unit.plan.plan_id,
                        claims=binding.claims,
                        run_artifacts=None,
                        non_attempt_receipt=receipt,
                    )
                )
                continue
            try:
                capability = binding.gate.authorize(plan=unit.plan, claims=binding.claims)
            except AITagShadowAuthorizationError as exc:
                if exc.reason_code == "budget_not_reserved":
                    disposition: Literal["skipped_budget", "not_run"] = "skipped_budget"
                    reason: LocalNonAttemptReason = "budget_not_reserved"
                    control_stage: NonAttemptControlStage = "budget_reservation"
                elif exc.reason_code == "egress_not_approved":
                    disposition = "not_run"
                    reason = "egress_not_approved"
                    control_stage = "egress_authorization"
                elif exc.reason_code == "credential_not_configured":
                    disposition = "not_run"
                    reason = "credential_not_configured"
                    control_stage = "credential_availability"
                else:
                    raise
                receipt = _non_attempt_receipt(
                    campaign_id=campaign.manifest.campaign_id,
                    plan_id=unit.plan.plan_id,
                    claims_id=binding.claims.claims_id,
                    disposition=disposition,
                    reason=reason,
                    observed_control_stage=control_stage,
                )
                record = _non_attempt_unit_execution(
                    campaign_id=campaign.manifest.campaign_id,
                    unit=unit,
                    claims=binding.claims,
                    receipt=receipt,
                )
                records.append(record)
                evidence_rows.append(
                    AITagShadowCampaignUnitEvidence(
                        plan_id=unit.plan.plan_id,
                        claims=binding.claims,
                        run_artifacts=None,
                        non_attempt_receipt=receipt,
                    )
                )
                continue

            elapsed_after_authorization_ms = max(
                0,
                (time.monotonic_ns() - started_ns) // 1_000_000,
            )
            remaining_after_authorization_ms = (
                limits.campaign_wall_clock_cap_ms - elapsed_after_authorization_ms
            )
            if remaining_after_authorization_ms < unit.plan.wall_clock_timeout_ms:
                binding.gate.revoke_unused_capability(
                    capability=capability,
                    plan=unit.plan,
                    claims=binding.claims,
                )
                receipt = _non_attempt_receipt(
                    campaign_id=campaign.manifest.campaign_id,
                    plan_id=unit.plan.plan_id,
                    claims_id=binding.claims.claims_id,
                    disposition="not_run",
                    reason="campaign_wall_clock_budget_insufficient",
                    observed_control_stage="campaign_deadline_post_authorization",
                    campaign_elapsed_ms=elapsed_after_authorization_ms,
                )
                records.append(
                    _non_attempt_unit_execution(
                        campaign_id=campaign.manifest.campaign_id,
                        unit=unit,
                        claims=binding.claims,
                        receipt=receipt,
                    )
                )
                evidence_rows.append(
                    AITagShadowCampaignUnitEvidence(
                        plan_id=unit.plan.plan_id,
                        claims=binding.claims,
                        run_artifacts=None,
                        non_attempt_receipt=receipt,
                    )
                )
                continue

            runner = (
                DeepSeekShadowRunner(gate=binding.gate)
                if binding.transport is None
                else DeepSeekShadowRunner(gate=binding.gate, transport=binding.transport)
            )
            try:
                artifacts = runner.run(
                    plan=unit.plan,
                    claims=binding.claims,
                    capability=capability,
                    envelope=unit.envelope,
                )
            except AITagShadowAuthorizationError as exc:
                if exc.reason_code != "credential_not_configured":
                    raise
                receipt = _non_attempt_receipt(
                    campaign_id=campaign.manifest.campaign_id,
                    plan_id=unit.plan.plan_id,
                    claims_id=binding.claims.claims_id,
                    disposition="not_run",
                    reason="credential_not_configured",
                    observed_control_stage="credential_availability",
                )
                records.append(
                    _non_attempt_unit_execution(
                        campaign_id=campaign.manifest.campaign_id,
                        unit=unit,
                        claims=binding.claims,
                        receipt=receipt,
                    )
                )
                evidence_rows.append(
                    AITagShadowCampaignUnitEvidence(
                        plan_id=unit.plan.plan_id,
                        claims=binding.claims,
                        run_artifacts=None,
                        non_attempt_receipt=receipt,
                    )
                )
                continue
            record = _attempted_unit_execution(
                campaign_id=campaign.manifest.campaign_id,
                unit=unit,
                claims=binding.claims,
                artifacts=artifacts,
            )
            records.append(record)
            evidence_rows.append(
                AITagShadowCampaignUnitEvidence(
                    plan_id=unit.plan.plan_id,
                    claims=binding.claims,
                    run_artifacts=artifacts,
                    non_attempt_receipt=None,
                )
            )

        result = _seal_execution_result(
            campaign_id=campaign.manifest.campaign_id,
            units=tuple(records),
            limits=limits,
        )
        execution_bundle = AITagShadowCampaignExecutionBundle(
            result=result,
            unit_evidence=tuple(evidence_rows),
        )
        verify_ai_tag_shadow_campaign_execution_result(
            result,
            trusted_upstream=trusted_upstream,
            expected_limits=limits,
            evidence_by_plan_id={item.plan_id: item for item in evidence_rows},
        )
        return execution_bundle


__all__ = [
    "AI_TAG_SHADOW_CAMPAIGN_EXECUTION_RESULT_SCHEMA_VERSION",
    "AI_TAG_SHADOW_CAMPAIGN_NON_ATTEMPT_RECEIPT_SCHEMA_VERSION",
    "AI_TAG_SHADOW_CAMPAIGN_UNIT_EXECUTION_SCHEMA_VERSION",
    "AITagShadowCampaignExecutionBundle",
    "AITagShadowCampaignExecutionCounts",
    "AITagShadowCampaignExecutionLimits",
    "AITagShadowCampaignExecutionResult",
    "AITagShadowCampaignLiveHarness",
    "AITagShadowCampaignNonAttemptReceipt",
    "AITagShadowCampaignRuntimeBinding",
    "AITagShadowCampaignTrustedUpstream",
    "AITagShadowCampaignUnitEvidence",
    "AITagShadowCampaignUnitExecution",
    "AttemptOutcome",
    "CampaignExecutionQualificationBlocker",
    "DispatchDisposition",
    "LocalNonAttemptReason",
    "NonAttemptControlStage",
    "load_ai_tag_shadow_campaign_execution_result",
    "load_ai_tag_shadow_campaign_non_attempt_receipt",
    "load_ai_tag_shadow_campaign_unit_execution",
    "seal_ai_tag_shadow_campaign_execution_result",
    "seal_ai_tag_shadow_campaign_non_attempt_receipt",
    "seal_ai_tag_shadow_campaign_unit_execution",
    "verify_ai_tag_shadow_campaign_execution_result",
]
