from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, NoReturn, Protocol, Self

from pydantic import Field, model_validator

from arkts_code_reviewer.code_analysis import (
    ChangeAtomInput,
    ChangedFileInput,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    load_json_object,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    DeepSeekCredentialUnavailableError,
    DeepSeekShadowHttpTransport,
    EnvironmentDeepSeekCredentialProvider,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
    verify_ai_tag_response_validation,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.repository_campaign_parser import (
    REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256,
    REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION,
    REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256,
    REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_REVISION,
    REPOSITORY_SYNTHETIC_CAMPAIGN_PARSER_PROFILE,
    REPOSITORY_SYNTHETIC_CAMPAIGN_PATH,
    REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY,
    RepositorySyntheticCampaignFileParser,
    RepositorySyntheticCampaignParserError,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    AITagShadowCampaignBundle,
    build_ai_tag_shadow_campaign,
    build_ai_tag_shadow_campaign_evaluation_report,
    verify_ai_tag_shadow_campaign_evaluation_report,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign_execution import (
    AITagShadowCampaignExecutionBundle,
    AITagShadowCampaignExecutionLimits,
    AITagShadowCampaignLiveHarness,
    AITagShadowCampaignRuntimeBinding,
    AITagShadowCampaignTrustedUpstream,
    verify_ai_tag_shadow_campaign_execution_result,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowTrustedPlanInputs,
)

AI_TAG_REPOSITORY_SYNTHETIC_CAMPAIGN_CASE_SCHEMA_VERSION: Literal[
    "ai-tag-repository-synthetic-campaign-case-v1"
] = "ai-tag-repository-synthetic-campaign-case-v1"
AI_TAG_LOCAL_CAMPAIGN_EGRESS_APPROVAL_SCHEMA_VERSION: Literal[
    "ai-tag-local-campaign-egress-approval-v1"
] = "ai-tag-local-campaign-egress-approval-v1"
AI_TAG_LOCAL_CAMPAIGN_PLAN_RESERVATION_SCHEMA_VERSION: Literal[
    "ai-tag-local-campaign-plan-reservation-v1"
] = "ai-tag-local-campaign-plan-reservation-v1"
AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION: Literal[
    "ai-tag-campaign-live-smoke-summary-v2"
] = "ai-tag-campaign-live-smoke-summary-v2"

REPOSITORY_SYNTHETIC_CAMPAIGN_CASE = "repository-synthetic-multi-unit-campaign-v1"
REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT = (
    "YES_REPOSITORY_PROMPT_TAXONOMY_AND_FIXED_SYNTHETIC_CAMPAIGN"
)

DEFAULT_CAMPAIGN_PLAN_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_CAMPAIGN_PLAN_TIMEOUT_MS = 60_000
DEFAULT_CAMPAIGN_PLAN_MAX_RESPONSE_BYTES = 2_000_000

_FIXED_REPOSITORY = REPOSITORY_SYNTHETIC_CAMPAIGN_REPOSITORY
_FIXED_BASE_REVISION = REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_REVISION
_FIXED_HEAD_REVISION = REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_REVISION
_FIXED_PATH = REPOSITORY_SYNTHETIC_CAMPAIGN_PATH
_FIXED_CHANGED_LINES = (5, 9)
_FIXED_CONTEXT_BUDGET = 20_000

_FIXED_BASE_CODE = """@Entry
@Component
struct CampaignProbe {
  first(): void {
    console.info(\"repository synthetic old first\")
  }
  second(): void {
    setTimeout(() => {
      console.info(\"repository synthetic old second\")
    }, 1000)
  }
}
"""
_FIXED_HEAD_CODE = """@Entry
@Component
struct CampaignProbe {
  first(): void {
    console.info(\"repository synthetic new first\")
  }
  second(): void {
    setTimeout(() => {
      console.info(\"repository synthetic new second\")
    }, 1000)
  }
}
"""
_FIXED_BASE_CODE_SHA256 = REPOSITORY_SYNTHETIC_CAMPAIGN_BASE_CODE_SHA256
_FIXED_HEAD_CODE_SHA256 = REPOSITORY_SYNTHETIC_CAMPAIGN_HEAD_CODE_SHA256

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_CASE_ID = rf"^ai-tag-repository-campaign-case:sha256:{_HASH}$"
_CAMPAIGN_ID = rf"^ai-tag-shadow-campaign:sha256:{_HASH}$"
_PLAN_SET_DIGEST = rf"^ai-tag-shadow-campaign-plan-set:sha256:{_HASH}$"
_CAPS_ID = rf"^ai-tag-shadow-campaign-caps:sha256:{_HASH}$"
_SOURCE_BUNDLE_ID = rf"^ai-tag-fixed-source-bundle:sha256:{_HASH}$"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_APPROVAL_ID = rf"^ai-egress-approval:sha256:{_HASH}$"
_RESERVATION_ID = rf"^ai-budget-reservation:sha256:{_HASH}$"


class CampaignSmokePreflightError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _RepositorySyntheticCampaignCapsPayload(FrozenModel):
    max_units: Annotated[int, Field(ge=1, le=64)]
    max_total_attempts: Annotated[int, Field(ge=1, le=64)]
    max_total_wire_body_bytes: Annotated[int, Field(ge=1, le=64_000_000)]
    max_total_output_tokens: Annotated[int, Field(ge=256, le=262_144)]
    max_total_response_bytes: Annotated[int, Field(ge=1_024, le=128_000_000)]
    campaign_wall_clock_cap_ms: Annotated[int, Field(ge=1_000, le=3_600_000)]


class RepositorySyntheticCampaignCaps(_RepositorySyntheticCampaignCapsPayload):
    caps_id: Annotated[str, Field(pattern=_CAPS_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-shadow-campaign-caps",
            identity_payload(self, "caps_id"),
        )
        if self.caps_id != expected:
            raise ValueError("repository Campaign cap identity does not match its contents")
        return self

    def execution_limits(self) -> AITagShadowCampaignExecutionLimits:
        return AITagShadowCampaignExecutionLimits(
            max_units=self.max_units,
            max_total_wire_body_bytes=self.max_total_wire_body_bytes,
            max_total_output_tokens=self.max_total_output_tokens,
            max_total_response_bytes=self.max_total_response_bytes,
            campaign_wall_clock_cap_ms=self.campaign_wall_clock_cap_ms,
        )


class _RepositorySyntheticCampaignCasePayload(FrozenModel):
    schema_version: Literal["ai-tag-repository-synthetic-campaign-case-v1"]
    case_name: Literal["repository-synthetic-multi-unit-campaign-v1"]
    origin: Literal["repository_authored_synthetic"]
    data_classification: Literal["repository_contained_synthetic_no_user_code"]
    source_policy: Literal["closed_package_assets_no_external_input"]
    outbound_asset_scope: Literal["repository_prompt_taxonomy_and_fixed_synthetic_campaign_code"]
    base_code_sha256: Annotated[str, Field(pattern=_SHA256)]
    head_code_sha256: Annotated[str, Field(pattern=_SHA256)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID)]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    plan_set_digest: Annotated[str, Field(pattern=_PLAN_SET_DIGEST)]
    caps_id: Annotated[str, Field(pattern=_CAPS_ID)]
    unit_count: Literal[4]
    execution_scope: Literal["shadow_only_no_hybrid_no_retrieval"]
    qualification: Literal["connectivity_and_contract_campaign_not_tag_truth"]


class RepositorySyntheticCampaignCase(_RepositorySyntheticCampaignCasePayload):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-repository-campaign-case",
            identity_payload(self, "case_id"),
        )
        if self.case_id != expected:
            raise ValueError("repository synthetic Campaign case identity does not match")
        return self


class _LocalCampaignEgressApprovalPayload(FrozenModel):
    schema_version: Literal["ai-tag-local-campaign-egress-approval-v1"]
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    plan_set_digest: Annotated[str, Field(pattern=_PLAN_SET_DIGEST)]
    caps_id: Annotated[str, Field(pattern=_CAPS_ID)]
    max_units: Annotated[int, Field(ge=1, le=64)]
    max_total_attempts: Annotated[int, Field(ge=1, le=64)]
    max_total_wire_body_bytes: Annotated[int, Field(ge=1, le=64_000_000)]
    max_total_output_tokens: Annotated[int, Field(ge=256, le=262_144)]
    max_total_response_bytes: Annotated[int, Field(ge=1_024, le=128_000_000)]
    campaign_wall_clock_cap_ms: Annotated[int, Field(ge=1_000, le=3_600_000)]
    operator_acknowledgement: Literal["YES_REPOSITORY_PROMPT_TAXONOMY_AND_FIXED_SYNTHETIC_CAMPAIGN"]
    outbound_asset_scope: Literal["repository_prompt_taxonomy_and_fixed_synthetic_campaign_code"]
    approval_scope: Literal["one_process_exact_repository_campaign_plan_set"]
    qualification: Literal["local_operator_control_not_deployment_compliance_approval"]


class LocalCampaignEgressApproval(_LocalCampaignEgressApprovalPayload):
    approval_id: Annotated[str, Field(pattern=_APPROVAL_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-egress-approval",
            identity_payload(self, "approval_id"),
        )
        if self.approval_id != expected:
            raise ValueError("local Campaign approval identity does not match its contents")
        return self


class _LocalCampaignPlanReservationPayload(FrozenModel):
    schema_version: Literal["ai-tag-local-campaign-plan-reservation-v1"]
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    plan_set_digest: Annotated[str, Field(pattern=_PLAN_SET_DIGEST)]
    caps_id: Annotated[str, Field(pattern=_CAPS_ID)]
    approval_id: Annotated[str, Field(pattern=_APPROVAL_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=120_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    reservation_scope: Literal["one_local_fixed_campaign_plan_attempt"]
    replay_guard: Literal["per_plan_atomic_local_marker"]
    qualification: Literal["local_attempt_cap_not_currency_or_provider_budget"]


class LocalCampaignPlanReservation(_LocalCampaignPlanReservationPayload):
    reservation_id: Annotated[str, Field(pattern=_RESERVATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-budget-reservation",
            identity_payload(self, "reservation_id"),
        )
        if self.reservation_id != expected:
            raise ValueError("local Campaign reservation identity does not match its contents")
        return self


@dataclass(frozen=True, repr=False)
class RepositorySyntheticCampaignBundle:
    case: RepositorySyntheticCampaignCase
    trusted_upstream: AITagShadowCampaignTrustedUpstream
    caps: RepositorySyntheticCampaignCaps

    @property
    def campaign(self) -> AITagShadowCampaignBundle:
        return self.trusted_upstream.bundle

    def __repr__(self) -> str:
        return "RepositorySyntheticCampaignBundle(<closed-package-campaign-roots>)"


@dataclass(frozen=True, repr=False)
class RepositorySyntheticCampaignRun:
    bundle: RepositorySyntheticCampaignBundle
    approval: LocalCampaignEgressApproval
    reservations: tuple[LocalCampaignPlanReservation, ...]
    execution: AITagShadowCampaignExecutionBundle

    def __repr__(self) -> str:
        return "RepositorySyntheticCampaignRun(<redacted-runtime-artifacts>)"


class CredentialProviderFactory(Protocol):
    def __call__(self) -> DeepSeekCredentialProvider: ...


def _content_sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _assert_fixed_source_identity() -> None:
    if (
        _content_sha256(_FIXED_BASE_CODE) != _FIXED_BASE_CODE_SHA256
        or _content_sha256(_FIXED_HEAD_CODE) != _FIXED_HEAD_CODE_SHA256
    ):
        raise CampaignSmokePreflightError("fixed_source_identity_mismatch")


def _fixed_snapshot(*, content: str, revision: str) -> CodeSourceSnapshot:
    source_ref = CodeSourceRef.create(
        repository=_FIXED_REPOSITORY,
        revision=revision,
        path=_FIXED_PATH,
        content_hash=_content_sha256(content),
    )
    return CodeSourceSnapshot(source_ref=source_ref, content=content)


def _plan_set_digest(campaign: AITagShadowCampaignBundle) -> str:
    rows = tuple(
        {
            "plan_id": unit.plan.plan_id,
            "wire_body_sha256": unit.plan.wire_body_sha256,
            "endpoint_url": unit.plan.endpoint_url,
            "model": unit.plan.wire_payload.model,
            "max_output_tokens": unit.plan.wire_payload.max_tokens,
            "wall_clock_timeout_ms": unit.plan.wall_clock_timeout_ms,
            "max_response_bytes": unit.plan.max_response_bytes,
            "max_attempts": unit.plan.max_attempts,
        }
        for unit in campaign.units
    )
    return canonical_hash(
        "ai-tag-shadow-campaign-plan-set",
        {"campaign_id": campaign.manifest.campaign_id, "plans": rows},
    )


def _build_caps(campaign: AITagShadowCampaignBundle) -> RepositorySyntheticCampaignCaps:
    return seal_payload(
        {
            "max_units": len(campaign.units),
            "max_total_attempts": sum(unit.plan.max_attempts for unit in campaign.units),
            "max_total_wire_body_bytes": sum(
                len(unit.plan.wire_body_json.encode("utf-8")) for unit in campaign.units
            ),
            "max_total_output_tokens": sum(
                unit.plan.wire_payload.max_tokens for unit in campaign.units
            ),
            "max_total_response_bytes": sum(
                unit.plan.max_response_bytes for unit in campaign.units
            ),
            "campaign_wall_clock_cap_ms": sum(
                unit.plan.wall_clock_timeout_ms for unit in campaign.units
            ),
        },
        payload_type=_RepositorySyntheticCampaignCapsPayload,
        sealed_type=RepositorySyntheticCampaignCaps,
        identity_field="caps_id",
        identity_prefix="ai-tag-shadow-campaign-caps",
        context="Repository Synthetic Campaign Caps",
    )


def build_repository_synthetic_campaign_bundle() -> RepositorySyntheticCampaignBundle:
    """Build the only Campaign this entry point is allowed to inspect or execute."""

    _assert_fixed_source_identity()
    base = _fixed_snapshot(content=_FIXED_BASE_CODE, revision=_FIXED_BASE_REVISION)
    head = _fixed_snapshot(content=_FIXED_HEAD_CODE, revision=_FIXED_HEAD_REVISION)
    atoms = tuple(
        ChangeAtomInput(
            kind="replacement",
            old_span=ReviewUnitSpan(line_number, line_number),
            new_span=ReviewUnitSpan(line_number, line_number),
            deleted_old_lines=(line_number,),
            added_new_lines=(line_number,),
        )
        for line_number in _FIXED_CHANGED_LINES
    )
    change_set = normalize_change_set(
        repository=_FIXED_REPOSITORY,
        base_revision=_FIXED_BASE_REVISION,
        head_revision=_FIXED_HEAD_REVISION,
        files=(
            ChangedFileInput(
                status="modified",
                old_path=_FIXED_PATH,
                new_path=_FIXED_PATH,
                old_snapshot=base,
                new_snapshot=head,
                atoms=atoms,
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    try:
        file_parser = RepositorySyntheticCampaignFileParser()
    except RepositorySyntheticCampaignParserError as exc:
        raise CampaignSmokePreflightError(str(exc)) from None
    analyzer = CodeAnalyzer(file_parser=file_parser)
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    if any(
        parse_result.analysis.parser_quality.layer != "L1"
        or parse_result.analysis.parser_quality.error_nodes != 0
        or parse_result.analysis.parser_quality.missing_nodes != 0
        or parse_result.analysis.parser_quality.warnings
        for parse_result in analysis.file_parse_results
    ):
        raise CampaignSmokePreflightError("fixed_campaign_parser_quality_mismatch")
    context_plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=_FIXED_CONTEXT_BUDGET,
    )
    expected_units = (
        ("base", "CampaignProbe.first", "method", 4, 6, False),
        ("base", "CampaignProbe.second", "method", 7, 11, False),
        ("head", "CampaignProbe.first", "method", 4, 6, False),
        ("head", "CampaignProbe.second", "method", 7, 11, False),
    )
    actual_units = tuple(
        sorted(
            (
                unit.source_role,
                unit.unit_symbol,
                unit.unit_kind,
                unit.source_span.start_line,
                unit.source_span.end_line,
                unit.context_degraded,
            )
            for unit in analysis.review_units
        )
    )
    if actual_units != expected_units:
        raise CampaignSmokePreflightError("fixed_campaign_unit_semantics_mismatch")
    unit_ids = tuple(sorted(unit.unit_id for unit in analysis.review_units))
    if len(unit_ids) != 4:
        raise CampaignSmokePreflightError("fixed_campaign_unit_count_mismatch")
    campaign = build_ai_tag_shadow_campaign(
        analysis_result=analysis,
        context_plan=context_plan,
        source_snapshots=snapshots,
        unit_ids=unit_ids,
        max_output_tokens=DEFAULT_CAMPAIGN_PLAN_MAX_OUTPUT_TOKENS,
        timeout_ms=DEFAULT_CAMPAIGN_PLAN_TIMEOUT_MS,
        max_response_bytes=DEFAULT_CAMPAIGN_PLAN_MAX_RESPONSE_BYTES,
        parser_profile=REPOSITORY_SYNTHETIC_CAMPAIGN_PARSER_PROFILE,
    )
    if any(unit.plan.max_attempts != 1 for unit in campaign.units):
        raise CampaignSmokePreflightError("fixed_campaign_plan_attempt_cap_invalid")
    caps = _build_caps(campaign)
    source_bundle_id = canonical_hash(
        "ai-tag-fixed-source-bundle",
        {
            "base_code_sha256": _FIXED_BASE_CODE_SHA256,
            "head_code_sha256": _FIXED_HEAD_CODE_SHA256,
        },
    )
    case = seal_payload(
        {
            "schema_version": AI_TAG_REPOSITORY_SYNTHETIC_CAMPAIGN_CASE_SCHEMA_VERSION,
            "case_name": REPOSITORY_SYNTHETIC_CAMPAIGN_CASE,
            "origin": "repository_authored_synthetic",
            "data_classification": "repository_contained_synthetic_no_user_code",
            "source_policy": "closed_package_assets_no_external_input",
            "outbound_asset_scope": (
                "repository_prompt_taxonomy_and_fixed_synthetic_campaign_code"
            ),
            "base_code_sha256": _FIXED_BASE_CODE_SHA256,
            "head_code_sha256": _FIXED_HEAD_CODE_SHA256,
            "source_bundle_id": source_bundle_id,
            "campaign_id": campaign.manifest.campaign_id,
            "plan_set_digest": _plan_set_digest(campaign),
            "caps_id": caps.caps_id,
            "unit_count": len(campaign.units),
            "execution_scope": "shadow_only_no_hybrid_no_retrieval",
            "qualification": "connectivity_and_contract_campaign_not_tag_truth",
        },
        payload_type=_RepositorySyntheticCampaignCasePayload,
        sealed_type=RepositorySyntheticCampaignCase,
        identity_field="case_id",
        identity_prefix="ai-tag-repository-campaign-case",
        context="Repository Synthetic Campaign Case",
    )
    trusted_upstream = AITagShadowCampaignTrustedUpstream(
        bundle=campaign,
        analysis_result=analysis,
        context_plan=context_plan,
        source_snapshots=snapshots,
        unit_ids=unit_ids,
        max_output_tokens=DEFAULT_CAMPAIGN_PLAN_MAX_OUTPUT_TOKENS,
        timeout_ms=DEFAULT_CAMPAIGN_PLAN_TIMEOUT_MS,
        max_response_bytes=DEFAULT_CAMPAIGN_PLAN_MAX_RESPONSE_BYTES,
        parser_profile=REPOSITORY_SYNTHETIC_CAMPAIGN_PARSER_PROFILE,
    )
    return RepositorySyntheticCampaignBundle(
        case=case,
        trusted_upstream=trusted_upstream,
        caps=caps,
    )


def _require_canonical_bundle(
    bundle: RepositorySyntheticCampaignBundle,
) -> RepositorySyntheticCampaignBundle:
    if not isinstance(bundle, RepositorySyntheticCampaignBundle):
        raise CampaignSmokePreflightError("fixed_campaign_bundle_not_trusted")
    expected = build_repository_synthetic_campaign_bundle()
    if bundle != expected:
        raise CampaignSmokePreflightError("fixed_campaign_bundle_not_trusted")
    return expected


def build_local_campaign_egress_approval(
    bundle: RepositorySyntheticCampaignBundle,
    *,
    approved_campaign_id: str,
    approved_plan_set_digest: str,
    cap_units: int,
    cap_total_attempts: int,
    cap_total_wire_body_bytes: int,
    cap_total_output_tokens: int,
    cap_total_response_bytes: int,
    cap_campaign_wall_clock_ms: int,
    acknowledgement: str,
) -> LocalCampaignEgressApproval:
    bundle = _require_canonical_bundle(bundle)
    caps = bundle.caps
    checks = (
        (approved_campaign_id, bundle.case.campaign_id, "approved_campaign_id_mismatch"),
        (
            approved_plan_set_digest,
            bundle.case.plan_set_digest,
            "approved_plan_set_digest_mismatch",
        ),
        (cap_units, caps.max_units, "cap_units_mismatch"),
        (cap_total_attempts, caps.max_total_attempts, "cap_total_attempts_mismatch"),
        (
            cap_total_wire_body_bytes,
            caps.max_total_wire_body_bytes,
            "cap_total_wire_body_bytes_mismatch",
        ),
        (
            cap_total_output_tokens,
            caps.max_total_output_tokens,
            "cap_total_output_tokens_mismatch",
        ),
        (
            cap_total_response_bytes,
            caps.max_total_response_bytes,
            "cap_total_response_bytes_mismatch",
        ),
        (
            cap_campaign_wall_clock_ms,
            caps.campaign_wall_clock_cap_ms,
            "cap_campaign_wall_clock_ms_mismatch",
        ),
    )
    for actual, expected, error_code in checks:
        if actual != expected:
            raise CampaignSmokePreflightError(error_code)
    if acknowledgement != REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT:
        raise CampaignSmokePreflightError("repository_campaign_acknowledgement_missing")
    return seal_payload(
        {
            "schema_version": AI_TAG_LOCAL_CAMPAIGN_EGRESS_APPROVAL_SCHEMA_VERSION,
            "case_id": bundle.case.case_id,
            "campaign_id": bundle.case.campaign_id,
            "plan_set_digest": bundle.case.plan_set_digest,
            "caps_id": caps.caps_id,
            "max_units": caps.max_units,
            "max_total_attempts": caps.max_total_attempts,
            "max_total_wire_body_bytes": caps.max_total_wire_body_bytes,
            "max_total_output_tokens": caps.max_total_output_tokens,
            "max_total_response_bytes": caps.max_total_response_bytes,
            "campaign_wall_clock_cap_ms": caps.campaign_wall_clock_cap_ms,
            "operator_acknowledgement": REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
            "outbound_asset_scope": (
                "repository_prompt_taxonomy_and_fixed_synthetic_campaign_code"
            ),
            "approval_scope": "one_process_exact_repository_campaign_plan_set",
            "qualification": "local_operator_control_not_deployment_compliance_approval",
        },
        payload_type=_LocalCampaignEgressApprovalPayload,
        sealed_type=LocalCampaignEgressApproval,
        identity_field="approval_id",
        identity_prefix="ai-egress-approval",
        context="Local Campaign Egress Approval",
    )


def build_local_campaign_plan_reservations(
    bundle: RepositorySyntheticCampaignBundle,
    *,
    approval: LocalCampaignEgressApproval,
) -> tuple[LocalCampaignPlanReservation, ...]:
    bundle = _require_canonical_bundle(bundle)
    canonical_approval = LocalCampaignEgressApproval.model_validate(
        approval.model_dump(mode="json")
    )
    expected_approval_fields = (
        bundle.case.case_id,
        bundle.case.campaign_id,
        bundle.case.plan_set_digest,
        bundle.caps.caps_id,
        bundle.caps.max_units,
        bundle.caps.max_total_attempts,
        bundle.caps.max_total_wire_body_bytes,
        bundle.caps.max_total_output_tokens,
        bundle.caps.max_total_response_bytes,
        bundle.caps.campaign_wall_clock_cap_ms,
    )
    actual_approval_fields = (
        canonical_approval.case_id,
        canonical_approval.campaign_id,
        canonical_approval.plan_set_digest,
        canonical_approval.caps_id,
        canonical_approval.max_units,
        canonical_approval.max_total_attempts,
        canonical_approval.max_total_wire_body_bytes,
        canonical_approval.max_total_output_tokens,
        canonical_approval.max_total_response_bytes,
        canonical_approval.campaign_wall_clock_cap_ms,
    )
    if actual_approval_fields != expected_approval_fields:
        raise CampaignSmokePreflightError("campaign_approval_not_trusted")
    reservations: list[LocalCampaignPlanReservation] = []
    for unit in bundle.campaign.units:
        plan = unit.plan
        reservations.append(
            seal_payload(
                {
                    "schema_version": (AI_TAG_LOCAL_CAMPAIGN_PLAN_RESERVATION_SCHEMA_VERSION),
                    "case_id": bundle.case.case_id,
                    "campaign_id": bundle.case.campaign_id,
                    "plan_set_digest": bundle.case.plan_set_digest,
                    "caps_id": bundle.caps.caps_id,
                    "approval_id": canonical_approval.approval_id,
                    "plan_id": plan.plan_id,
                    "wire_body_sha256": plan.wire_body_sha256,
                    "max_output_tokens": plan.wire_payload.max_tokens,
                    "wall_clock_timeout_ms": plan.wall_clock_timeout_ms,
                    "max_response_bytes": plan.max_response_bytes,
                    "max_attempts": plan.max_attempts,
                    "reservation_scope": "one_local_fixed_campaign_plan_attempt",
                    "replay_guard": "per_plan_atomic_local_marker",
                    "qualification": ("local_attempt_cap_not_currency_or_provider_budget"),
                },
                payload_type=_LocalCampaignPlanReservationPayload,
                sealed_type=LocalCampaignPlanReservation,
                identity_field="reservation_id",
                identity_prefix="ai-budget-reservation",
                context="Local Campaign Plan Reservation",
            )
        )
    return tuple(reservations)


class OneShotCampaignPlanApprovalVerifier:
    """Consume one process-local Campaign approval once for one frozen Plan."""

    def __init__(
        self,
        *,
        bundle: RepositorySyntheticCampaignBundle,
        approval: LocalCampaignEgressApproval,
        expected_plan: AITagShadowDispatchPlan,
    ) -> None:
        self._case = RepositorySyntheticCampaignCase.model_validate(
            bundle.case.model_dump(mode="json")
        )
        self._caps = RepositorySyntheticCampaignCaps.model_validate(
            bundle.caps.model_dump(mode="json")
        )
        self._approval = LocalCampaignEgressApproval.model_validate(
            approval.model_dump(mode="json")
        )
        self._expected_plan = AITagShadowDispatchPlan.model_validate(
            expected_plan.model_dump(mode="json")
        )
        self._consumed = False
        self._lock = threading.Lock()

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        approval = self._approval
        if (
            plan != self._expected_plan
            or approval_id != approval.approval_id
            or approval.case_id != self._case.case_id
            or approval.campaign_id != self._case.campaign_id
            or approval.plan_set_digest != self._case.plan_set_digest
            or approval.caps_id != self._caps.caps_id
            or approval.max_units != self._caps.max_units
            or approval.max_total_attempts != self._caps.max_total_attempts
            or approval.max_total_wire_body_bytes != self._caps.max_total_wire_body_bytes
            or approval.max_total_output_tokens != self._caps.max_total_output_tokens
            or approval.max_total_response_bytes != self._caps.max_total_response_bytes
            or approval.campaign_wall_clock_cap_ms != self._caps.campaign_wall_clock_cap_ms
        ):
            raise AITagShadowAuthorizationError("egress_not_approved")
        with self._lock:
            if self._consumed:
                raise AITagShadowAuthorizationError("egress_not_approved")
            self._consumed = True


class AtomicCampaignPlanBudgetLedger:
    """Write one Plan marker before dispatch; it is not external or cost authority."""

    def __init__(
        self,
        *,
        bundle: RepositorySyntheticCampaignBundle,
        reservation: LocalCampaignPlanReservation,
        expected_plan: AITagShadowDispatchPlan,
        state_dir: Path,
    ) -> None:
        self._case = RepositorySyntheticCampaignCase.model_validate(
            bundle.case.model_dump(mode="json")
        )
        self._caps = RepositorySyntheticCampaignCaps.model_validate(
            bundle.caps.model_dump(mode="json")
        )
        self._reservation = LocalCampaignPlanReservation.model_validate(
            reservation.model_dump(mode="json")
        )
        self._expected_plan = AITagShadowDispatchPlan.model_validate(
            expected_plan.model_dump(mode="json")
        )
        self._state_dir = Path(state_dir)
        self._lock = threading.Lock()

    @property
    def marker_path(self) -> Path:
        return self._state_dir / _campaign_marker_name(self._reservation)

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        reservation = self._reservation
        if (
            plan != self._expected_plan
            or reservation_id != reservation.reservation_id
            or reservation.case_id != self._case.case_id
            or reservation.campaign_id != self._case.campaign_id
            or reservation.plan_set_digest != self._case.plan_set_digest
            or reservation.caps_id != self._caps.caps_id
            or reservation.plan_id != plan.plan_id
            or reservation.wire_body_sha256 != plan.wire_body_sha256
            or reservation.max_output_tokens != plan.wire_payload.max_tokens
            or reservation.wall_clock_timeout_ms != plan.wall_clock_timeout_ms
            or reservation.max_response_bytes != plan.max_response_bytes
            or reservation.max_attempts != plan.max_attempts
        ):
            raise AITagShadowAuthorizationError("budget_not_reserved")
        with self._lock:
            self._consume_marker(plan)

    def _consume_marker(self, plan: AITagShadowDispatchPlan) -> None:
        marker = canonical_json(
            {
                "schema_version": "ai-tag-local-campaign-attempt-consumption-v1",
                "case_id": self._case.case_id,
                "campaign_id": self._case.campaign_id,
                "plan_id": plan.plan_id,
                "wire_body_sha256": plan.wire_body_sha256,
                "reservation_id": self._reservation.reservation_id,
                "qualification": ("local_replay_guard_not_provider_or_cost_evidence"),
            }
        ).encode("utf-8")
        state_descriptor: int | None = None
        marker_descriptor: int | None = None
        try:
            opened_state_descriptor = _open_campaign_state_directory(
                self._state_dir,
                create=True,
            )
            if opened_state_descriptor is None:  # pragma: no cover - create=True invariant
                raise OSError("Campaign state directory was not created")
            state_descriptor = opened_state_descriptor
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            marker_descriptor = os.open(
                _campaign_marker_name(self._reservation),
                flags,
                0o600,
                dir_fd=state_descriptor,
            )
            os.fchmod(marker_descriptor, 0o600)
        except (FileExistsError, OSError, ValueError):
            if marker_descriptor is not None:
                os.close(marker_descriptor)
            if state_descriptor is not None:
                os.close(state_descriptor)
            raise AITagShadowAuthorizationError("budget_not_reserved") from None
        assert marker_descriptor is not None
        assert state_descriptor is not None
        try:
            if os.write(marker_descriptor, marker) != len(marker):
                raise OSError("incomplete local reservation marker")
            os.fsync(marker_descriptor)
            os.fsync(state_descriptor)
        except OSError:
            raise AITagShadowAuthorizationError("budget_not_reserved") from None
        finally:
            os.close(marker_descriptor)
            os.close(state_descriptor)


def _campaign_marker_name(reservation: LocalCampaignPlanReservation) -> str:
    digest = reservation.reservation_id.rsplit(":", 1)[-1]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("invalid Campaign reservation digest")
    return f"{digest}.consumed.json"


def _campaign_result_artifact_name(execution_result_id: str) -> str:
    digest = execution_result_id.rsplit(":", 1)[-1]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("invalid Campaign Execution Result digest")
    return f"{digest}.campaign-result.json"


def _seal_campaign_run_summary(payload: Mapping[str, object]) -> dict[str, object]:
    if "summary_id" in payload:
        raise ValueError("unsealed Campaign run summary cannot contain summary_id")
    sealed = dict(payload)
    sealed["summary_id"] = canonical_hash(
        "ai-tag-campaign-live-smoke-summary",
        sealed,
    )
    _verify_campaign_run_summary(sealed)
    return sealed


def _verify_campaign_run_summary(summary: Mapping[str, object]) -> None:
    if (
        summary.get("schema_version") != AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION
        or summary.get("mode") != "live_shadow_campaign_result"
    ):
        raise ValueError("Campaign run summary has an unsupported contract")
    execution_result_id = summary.get("execution_result_id")
    summary_id = summary.get("summary_id")
    if not isinstance(execution_result_id, str) or not isinstance(summary_id, str):
        raise ValueError("Campaign run summary identities are missing")
    if summary.get("result_artifact_name") != _campaign_result_artifact_name(execution_result_id):
        raise ValueError("Campaign run summary artifact name is invalid")
    summary_identity_payload = dict(summary)
    summary_identity_payload.pop("summary_id", None)
    if summary_id != canonical_hash(
        "ai-tag-campaign-live-smoke-summary",
        summary_identity_payload,
    ):
        raise ValueError("Campaign run summary identity does not match its contents")


def load_campaign_run_summary(raw: str | bytes) -> dict[str, object]:
    summary = load_json_object(raw, "AI Tag Campaign Live Smoke Summary")
    _verify_campaign_run_summary(summary)
    return summary


def _campaign_state_entry(state_dir: Path) -> tuple[Path, str]:
    state_dir = Path(state_dir)
    entry_name = state_dir.name
    if not entry_name or entry_name in {".", ".."}:
        raise ValueError("unsafe Campaign state directory")
    return state_dir.parent, entry_name


def _open_real_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    has_nofollow = hasattr(os, "O_NOFOLLOW")
    if has_nofollow:
        flags |= os.O_NOFOLLOW

    before: os.stat_result | None = None
    if not has_nofollow:
        before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("unsafe Campaign directory")

    descriptor = os.open(path, flags, dir_fd=dir_fd)
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISDIR(after.st_mode):
            raise ValueError("unsafe Campaign directory")
        if before is not None and (before.st_dev, before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ):
            raise ValueError("Campaign directory changed while opening")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _validate_private_state_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Campaign state directory must not be group/world accessible")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise ValueError("Campaign state directory must be owned by the current user")


def _open_campaign_state_directory(
    state_dir: Path,
    *,
    create: bool,
) -> int | None:
    parent_path, entry_name = _campaign_state_entry(state_dir)
    parent_descriptor = _open_real_directory(parent_path)
    try:
        try:
            state_descriptor = _open_real_directory(
                entry_name,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            if not create:
                return None
            try:
                os.mkdir(entry_name, mode=0o700, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except FileExistsError:
                # A concurrent creator is acceptable only if the resulting entry
                # is itself a private, real directory.
                pass
            state_descriptor = _open_real_directory(
                entry_name,
                dir_fd=parent_descriptor,
            )
        try:
            _validate_private_state_directory(state_descriptor)
        except BaseException:
            os.close(state_descriptor)
            raise
        return state_descriptor
    finally:
        os.close(parent_descriptor)


def _persist_campaign_run_summary(
    *,
    state_dir: Path,
    summary: dict[str, object],
    execution_result_id: str,
) -> None:
    _verify_campaign_run_summary(summary)
    artifact_name = _campaign_result_artifact_name(execution_result_id)
    if (
        summary.get("execution_result_id") != execution_result_id
        or summary.get("result_artifact_name") != artifact_name
    ):
        raise ValueError("Campaign result summary differs from its artifact identity")
    encoded = canonical_json(summary).encode("utf-8")
    temporary_name = f".{artifact_name}.tmp"
    state_descriptor: int | None = None
    artifact_descriptor: int | None = None
    temporary_created = False
    try:
        state_descriptor = _open_campaign_state_directory(state_dir, create=True)
        if state_descriptor is None:  # pragma: no cover - create=True invariant
            raise OSError("Campaign state directory was not created")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        artifact_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=state_descriptor,
        )
        temporary_created = True
        os.fchmod(artifact_descriptor, 0o600)
        written = 0
        while written < len(encoded):
            count = os.write(artifact_descriptor, encoded[written:])
            if count <= 0:
                raise OSError("incomplete Campaign result artifact")
            written += count
        os.fsync(artifact_descriptor)
        os.link(
            temporary_name,
            artifact_name,
            src_dir_fd=state_descriptor,
            dst_dir_fd=state_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=state_descriptor)
        temporary_created = False
        os.fsync(state_descriptor)
    finally:
        if artifact_descriptor is not None:
            os.close(artifact_descriptor)
        if temporary_created and state_descriptor is not None:
            try:
                os.unlink(temporary_name, dir_fd=state_descriptor)
            except OSError:
                pass
        if state_descriptor is not None:
            os.close(state_descriptor)


class _InjectedTransportCredentialProvider:
    @property
    def credential_scope_id(self) -> str:
        return canonical_hash(
            "deepseek-credential-scope",
            {"source": "injected-campaign-test-transport"},
        )

    def is_configured(self) -> bool:
        return True

    def get_api_key(self) -> NoReturn:
        raise DeepSeekCredentialUnavailableError(
            "injected Campaign transport cannot access a provider credential"
        )


class _CampaignScopedCredentialProvider:
    """Bind one Campaign to one credential deployment identity snapshot."""

    def __init__(self, provider: DeepSeekCredentialProvider) -> None:
        self._provider = provider
        self._credential_scope_id = provider.credential_scope_id

    @property
    def credential_scope_id(self) -> str:
        return self._credential_scope_id

    def is_configured(self) -> bool:
        return self._provider.is_configured()

    def get_api_key(self) -> str:
        return self._provider.get_api_key()


def _validate_state_preflight(
    state_dir: Path,
    reservations: tuple[LocalCampaignPlanReservation, ...],
) -> None:
    state_descriptor: int | None = None
    try:
        state_descriptor = _open_campaign_state_directory(
            state_dir,
            create=False,
        )
        if state_descriptor is not None:
            for reservation in reservations:
                marker_name = _campaign_marker_name(reservation)
                try:
                    os.stat(
                        marker_name,
                        dir_fd=state_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                raise CampaignSmokePreflightError("campaign_plan_already_reserved")
            return

        # Opening the real parent is part of _open_campaign_state_directory even
        # when the state entry does not exist. No path-based check is repeated.
        return
    except CampaignSmokePreflightError:
        raise
    except (OSError, ValueError):
        raise CampaignSmokePreflightError("unsafe_state_directory") from None
    finally:
        if state_descriptor is not None:
            os.close(state_descriptor)


def _campaign_trust_domain_id(bundle: RepositorySyntheticCampaignBundle) -> str:
    return canonical_hash(
        "ai-shadow-trust-domain",
        {
            "scope": "local-repository-synthetic-campaign-v1",
            "case_id": bundle.case.case_id,
            "campaign_id": bundle.case.campaign_id,
        },
    )


def _build_claims(
    *,
    bundle: RepositorySyntheticCampaignBundle,
    plan: AITagShadowDispatchPlan,
    approval: LocalCampaignEgressApproval,
    reservation: LocalCampaignPlanReservation,
    credential_scope_id: str,
) -> AITagShadowDispatchClaims:
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": plan.plan_id,
            "trust_domain_id": _campaign_trust_domain_id(bundle),
            "egress_approval_id": approval.approval_id,
            "budget_reservation_id": reservation.reservation_id,
            "credential_scope_id": credential_scope_id,
            "egress_scope": "exact_wire_body_sha256",
            "budget_scope": "one_attempt_worst_case_reserved",
            "qualification": "references_require_runtime_verification",
        }
    )


def _build_runtime_bindings(
    *,
    bundle: RepositorySyntheticCampaignBundle,
    approval: LocalCampaignEgressApproval,
    reservations: tuple[LocalCampaignPlanReservation, ...],
    state_dir: Path,
    credential_provider: DeepSeekCredentialProvider,
    transport: DeepSeekShadowHttpTransport | None,
) -> dict[str, AITagShadowCampaignRuntimeBinding]:
    if len(reservations) != len(bundle.campaign.units):
        raise CampaignSmokePreflightError("campaign_reservation_coverage_mismatch")
    scoped_credential_provider = _CampaignScopedCredentialProvider(credential_provider)
    bindings: dict[str, AITagShadowCampaignRuntimeBinding] = {}
    for unit, reservation in zip(bundle.campaign.units, reservations, strict=True):
        plan = unit.plan
        if reservation.plan_id != plan.plan_id:
            raise CampaignSmokePreflightError("campaign_reservation_order_mismatch")
        trusted_plan_inputs = AITagShadowTrustedPlanInputs(
            envelope=unit.envelope,
            card=unit.card,
            context_policy=bundle.campaign.context_policy,
            max_output_tokens=plan.wire_payload.max_tokens,
            wall_clock_timeout_ms=plan.wall_clock_timeout_ms,
            max_response_bytes=plan.max_response_bytes,
        )
        gate = AITagShadowAuthorizationGate(
            trust_domain_id=_campaign_trust_domain_id(bundle),
            credential_provider=scoped_credential_provider,
            trusted_plan_inputs=trusted_plan_inputs,
            egress_verifier=OneShotCampaignPlanApprovalVerifier(
                bundle=bundle,
                approval=approval,
                expected_plan=plan,
            ),
            budget_ledger=AtomicCampaignPlanBudgetLedger(
                bundle=bundle,
                reservation=reservation,
                expected_plan=plan,
                state_dir=state_dir,
            ),
        )
        claims = _build_claims(
            bundle=bundle,
            plan=plan,
            approval=approval,
            reservation=reservation,
            credential_scope_id=gate.credential_scope_id,
        )
        bindings[plan.plan_id] = AITagShadowCampaignRuntimeBinding(
            claims=claims,
            gate=gate,
            transport=transport,
        )
    return bindings


def run_repository_synthetic_campaign(
    *,
    approved_campaign_id: str,
    approved_plan_set_digest: str,
    cap_units: int,
    cap_total_attempts: int,
    cap_total_wire_body_bytes: int,
    cap_total_output_tokens: int,
    cap_total_response_bytes: int,
    cap_campaign_wall_clock_ms: int,
    acknowledgement: str,
    state_dir: Path,
    credential_provider: DeepSeekCredentialProvider,
    transport: DeepSeekShadowHttpTransport | None = None,
    allow_real_transport: bool = False,
    allow_injected_transport: bool = False,
) -> RepositorySyntheticCampaignRun:
    """Execute only the package-owned fixed Campaign through the canonical Harness."""

    if type(allow_real_transport) is not bool or type(allow_injected_transport) is not bool:
        raise TypeError("Campaign transport allow flags must be bool values")
    if transport is None:
        if not allow_real_transport or allow_injected_transport:
            raise CampaignSmokePreflightError("real_transport_not_explicitly_allowed")
    elif allow_real_transport or not allow_injected_transport:
        raise CampaignSmokePreflightError("injected_transport_not_explicitly_allowed")
    bundle = build_repository_synthetic_campaign_bundle()
    approval = build_local_campaign_egress_approval(
        bundle,
        approved_campaign_id=approved_campaign_id,
        approved_plan_set_digest=approved_plan_set_digest,
        cap_units=cap_units,
        cap_total_attempts=cap_total_attempts,
        cap_total_wire_body_bytes=cap_total_wire_body_bytes,
        cap_total_output_tokens=cap_total_output_tokens,
        cap_total_response_bytes=cap_total_response_bytes,
        cap_campaign_wall_clock_ms=cap_campaign_wall_clock_ms,
        acknowledgement=acknowledgement,
    )
    reservations = build_local_campaign_plan_reservations(
        bundle,
        approval=approval,
    )
    _validate_state_preflight(Path(state_dir), reservations)
    bindings = _build_runtime_bindings(
        bundle=bundle,
        approval=approval,
        reservations=reservations,
        state_dir=Path(state_dir),
        credential_provider=credential_provider,
        transport=transport,
    )
    execution = AITagShadowCampaignLiveHarness().execute(
        trusted_upstream=bundle.trusted_upstream,
        runtime_bindings_by_plan_id=bindings,
        limits=bundle.caps.execution_limits(),
        allow_live_transport=allow_real_transport,
        allow_injected_transport=allow_injected_transport,
    )
    return RepositorySyntheticCampaignRun(
        bundle=bundle,
        approval=approval,
        reservations=reservations,
        execution=execution,
    )


def build_campaign_inspection_summary(
    bundle: RepositorySyntheticCampaignBundle,
) -> dict[str, object]:
    bundle = _require_canonical_bundle(bundle)
    plans = tuple(
        {
            "ordinal": ordinal,
            "card_id": unit.card.card_id,
            "model_view_id": unit.model_view.model_view_id,
            "request_id": unit.request.request_id,
            "envelope_id": unit.envelope.envelope_id,
            "plan_id": unit.plan.plan_id,
            "wire_body_sha256": unit.plan.wire_body_sha256,
            "wire_body_size_bytes": len(unit.plan.wire_body_json.encode("utf-8")),
            "endpoint_url": unit.plan.endpoint_url,
            "model": unit.plan.wire_payload.model,
            "max_output_tokens": unit.plan.wire_payload.max_tokens,
            "wall_clock_timeout_ms": unit.plan.wall_clock_timeout_ms,
            "max_response_bytes": unit.plan.max_response_bytes,
            "max_attempts": unit.plan.max_attempts,
        }
        for ordinal, unit in enumerate(bundle.campaign.units, start=1)
    )
    return {
        "schema_version": AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION,
        "mode": "inspect_only",
        "network_attempted": False,
        "credential_accessed": False,
        "case_id": bundle.case.case_id,
        "case_name": bundle.case.case_name,
        "data_classification": bundle.case.data_classification,
        "source_policy": bundle.case.source_policy,
        "outbound_asset_scope": bundle.case.outbound_asset_scope,
        "campaign_id": bundle.case.campaign_id,
        "plan_set_digest": bundle.case.plan_set_digest,
        "caps": bundle.caps.model_dump(mode="json"),
        "plans": plans,
        "required_acknowledgement": REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
        "required_runtime_authorization": (
            "exact_campaign_plan_set_caps_ack_and_per_plan_atomic_reservation"
        ),
        "execution_policy": "canonical_order_per_plan_single_attempt_no_retry_v1",
        "output_scope": "metadata_only_no_code_prompt_body_response_reason_or_state_path",
        "evidence_qualification_status": "not_qualified",
        "production_qualified": False,
    }


def _network_attempted_from_execution(
    execution: AITagShadowCampaignExecutionBundle,
) -> bool | None:
    counts = execution.result.counts
    if counts.network_observed_attempt_count > 0:
        return True
    if counts.injected_transport_attempt_count > 0:
        return None
    return False


def _build_run_summary(run: RepositorySyntheticCampaignRun) -> dict[str, object]:
    result = run.execution.result
    evidence_by_plan_id = {item.plan_id: item for item in run.execution.unit_evidence}
    verify_ai_tag_shadow_campaign_execution_result(
        result,
        trusted_upstream=run.bundle.trusted_upstream,
        expected_limits=run.bundle.caps.execution_limits(),
        evidence_by_plan_id=evidence_by_plan_id,
    )
    campaign_units = {item.plan.plan_id: item for item in run.bundle.campaign.units}
    response_validations: dict[str, AITagResponseValidation] = {}
    plans: list[dict[str, object]] = []
    for ordinal, unit in enumerate(result.units, start=1):
        evidence = evidence_by_plan_id[unit.plan_id]
        campaign_unit = campaign_units[unit.plan_id]
        validation = (
            None if evidence.run_artifacts is None else evidence.run_artifacts.response_validation
        )
        decisions: list[dict[str, str]] = []
        if validation is not None:
            verify_ai_tag_response_validation(validation, campaign_unit.envelope)
            response_validations[unit.plan_id] = validation
            if validation.status == "valid_shape":
                decisions = [
                    {"tag_id": judgment.tag_id, "decision": judgment.decision}
                    for judgment in validation.judgments
                ]
        if (unit.attempt_outcome == "valid_shape") != (len(decisions) == 24):
            raise ValueError("Campaign summary Tag decisions differ from execution status")
        decision_counts: dict[str, int] = {}
        for decision in decisions:
            value = decision["decision"]
            decision_counts[value] = decision_counts.get(value, 0) + 1
        plans.append(
            {
                "ordinal": ordinal,
                "plan_id": unit.plan_id,
                "card_id": campaign_unit.card.card_id,
                "source_role": campaign_unit.card.source_role,
                "unit_kind": campaign_unit.card.unit_kind,
                "unit_symbol": campaign_unit.card.unit_symbol,
                "dispatch_disposition": unit.dispatch_disposition,
                "attempt_outcome": unit.attempt_outcome,
                "attempt_count": unit.attempt_count,
                "transport_evidence": unit.transport_evidence,
                "network_observation": unit.network_observation,
                "response_validation_id": (
                    None if validation is None else validation.validation_id
                ),
                "validated_tag_decision_count": len(decisions),
                "decision_counts": decision_counts,
                "validated_tag_decisions": decisions,
            }
        )
    evaluation_report_id: str | None = None
    if len(response_validations) == len(run.bundle.campaign.units):
        report = build_ai_tag_shadow_campaign_evaluation_report(
            run.bundle.campaign,
            response_validations,
        )
        verify_ai_tag_shadow_campaign_evaluation_report(
            report,
            run.bundle.campaign,
            response_validations,
        )
        evaluation_report_id = report.report_id
    return _seal_campaign_run_summary(
        {
            "schema_version": AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION,
            "mode": "live_shadow_campaign_result",
            "network_attempted": _network_attempted_from_execution(run.execution),
            "network_observation_scope": (
                "fixed_httpx_observation_or_unknown_for_injected_transport"
            ),
            "dispatch_attempted": result.counts.attempted_unit_count > 0,
            "case_id": run.bundle.case.case_id,
            "campaign_id": result.campaign_id,
            "plan_set_digest": run.bundle.case.plan_set_digest,
            "execution_result_id": result.execution_result_id,
            "result_artifact_name": _campaign_result_artifact_name(result.execution_result_id),
            "result_artifact_scope": "safe_summary_not_full_evidence_graph",
            "evaluation_report_id": evaluation_report_id,
            "counts": result.counts.model_dump(mode="json"),
            "plans": tuple(plans),
            "raw_response_retained": False,
            "output_scope": (
                "metadata_and_validated_tag_decisions_no_code_prompt_body_response_"
                "reason_key_or_state_path"
            ),
            "evidence_qualification_status": result.evidence_qualification_status,
            "production_qualified": result.production_qualified,
        }
    )


def _safe_error_summary(*, code: str, attempted: bool | None) -> dict[str, object]:
    return {
        "schema_version": AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION,
        "mode": "campaign_preflight" if attempted is False else "campaign_attempt",
        "network_attempted": attempted,
        "error_code": code,
        "output_scope": "metadata_only_no_code_prompt_body_response_reason_key_or_state_path",
        "evidence_qualification_status": "not_qualified",
        "production_qualified": False,
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise CampaignSmokePreflightError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description=(
            "Inspect or explicitly run the package-owned fixed synthetic multi-Unit "
            "DeepSeek shadow Campaign."
        )
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--allow-real-transport", action="store_true")
    parser.add_argument("--approve-campaign-id")
    parser.add_argument("--approve-plan-set-digest")
    parser.add_argument("--cap-units", type=int)
    parser.add_argument("--cap-total-attempts", type=int)
    parser.add_argument("--cap-total-wire-body-bytes", type=int)
    parser.add_argument("--cap-total-output-tokens", type=int)
    parser.add_argument("--cap-total-response-bytes", type=int)
    parser.add_argument("--cap-campaign-wall-clock-ms", type=int)
    parser.add_argument("--acknowledge-repository-assets-and-synthetic-code")
    parser.add_argument("--state-dir", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_provider_factory: CredentialProviderFactory = (
        EnvironmentDeepSeekCredentialProvider
    ),
    test_transport: DeepSeekShadowHttpTransport | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
        bundle = build_repository_synthetic_campaign_bundle()
    except (CampaignSmokePreflightError, TypeError, ValueError):
        print(
            canonical_json(
                _safe_error_summary(code="fixture_or_arguments_invalid", attempted=False)
            )
        )
        return 2
    if not args.execute_live:
        print(canonical_json(build_campaign_inspection_summary(bundle)))
        return 2
    if test_transport is None and not args.allow_real_transport:
        print(
            canonical_json(
                _safe_error_summary(code="real_transport_not_explicitly_allowed", attempted=False)
            )
        )
        return 2
    if test_transport is not None and args.allow_real_transport:
        print(canonical_json(_safe_error_summary(code="transport_mode_conflict", attempted=False)))
        return 2
    required = (
        args.approve_campaign_id,
        args.approve_plan_set_digest,
        args.cap_units,
        args.cap_total_attempts,
        args.cap_total_wire_body_bytes,
        args.cap_total_output_tokens,
        args.cap_total_response_bytes,
        args.cap_campaign_wall_clock_ms,
        args.acknowledge_repository_assets_and_synthetic_code,
        args.state_dir,
    )
    if any(value is None for value in required):
        print(canonical_json(_safe_error_summary(code="live_controls_incomplete", attempted=False)))
        return 2
    try:
        build_local_campaign_egress_approval(
            bundle,
            approved_campaign_id=args.approve_campaign_id,
            approved_plan_set_digest=args.approve_plan_set_digest,
            cap_units=args.cap_units,
            cap_total_attempts=args.cap_total_attempts,
            cap_total_wire_body_bytes=args.cap_total_wire_body_bytes,
            cap_total_output_tokens=args.cap_total_output_tokens,
            cap_total_response_bytes=args.cap_total_response_bytes,
            cap_campaign_wall_clock_ms=args.cap_campaign_wall_clock_ms,
            acknowledgement=args.acknowledge_repository_assets_and_synthetic_code,
        )
    except (CampaignSmokePreflightError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, CampaignSmokePreflightError) else "controls_invalid"
        print(canonical_json(_safe_error_summary(code=code, attempted=False)))
        return 2
    if test_transport is None:
        try:
            credential_provider = credential_provider_factory()
        except Exception:
            print(
                canonical_json(
                    _safe_error_summary(code="credential_provider_unavailable", attempted=False)
                )
            )
            return 2
    else:
        credential_provider = _InjectedTransportCredentialProvider()
    try:
        run = run_repository_synthetic_campaign(
            approved_campaign_id=args.approve_campaign_id,
            approved_plan_set_digest=args.approve_plan_set_digest,
            cap_units=args.cap_units,
            cap_total_attempts=args.cap_total_attempts,
            cap_total_wire_body_bytes=args.cap_total_wire_body_bytes,
            cap_total_output_tokens=args.cap_total_output_tokens,
            cap_total_response_bytes=args.cap_total_response_bytes,
            cap_campaign_wall_clock_ms=args.cap_campaign_wall_clock_ms,
            acknowledgement=args.acknowledge_repository_assets_and_synthetic_code,
            state_dir=args.state_dir,
            credential_provider=credential_provider,
            transport=test_transport,
            allow_real_transport=test_transport is None,
            allow_injected_transport=test_transport is not None,
        )
    except CampaignSmokePreflightError as exc:
        print(canonical_json(_safe_error_summary(code=exc.code, attempted=False)))
        return 2
    except AITagShadowAuthorizationError as exc:
        print(canonical_json(_safe_error_summary(code=exc.reason_code, attempted=None)))
        return 3
    except (TypeError, ValueError):
        print(
            canonical_json(
                _safe_error_summary(code="campaign_runtime_or_integrity_invalid", attempted=None)
            )
        )
        return 3
    except Exception:
        print(canonical_json(_safe_error_summary(code="campaign_runtime_error", attempted=None)))
        return 3
    try:
        summary = _build_run_summary(run)
    except (TypeError, ValueError):
        try:
            network_attempted = _network_attempted_from_execution(run.execution)
        except (TypeError, ValueError):
            network_attempted = None
        print(
            canonical_json(
                _safe_error_summary(
                    code="campaign_summary_invalid",
                    attempted=network_attempted,
                )
            )
        )
        return 3
    try:
        _persist_campaign_run_summary(
            state_dir=args.state_dir,
            summary=summary,
            execution_result_id=run.execution.result.execution_result_id,
        )
    except (OSError, TypeError, ValueError):
        print(
            canonical_json(
                _safe_error_summary(
                    code="campaign_result_persistence_failed",
                    attempted=_network_attempted_from_execution(run.execution),
                )
            )
        )
        return 3
    print(canonical_json(summary))
    counts = run.execution.result.counts
    if (
        counts.attempted_unit_count == counts.planned_unit_count
        and counts.valid_shape_count == counts.planned_unit_count
    ):
        return 0
    return 3 if counts.attempted_unit_count > 0 else 2


__all__ = [
    "AI_TAG_CAMPAIGN_LIVE_SMOKE_SUMMARY_SCHEMA_VERSION",
    "AI_TAG_LOCAL_CAMPAIGN_EGRESS_APPROVAL_SCHEMA_VERSION",
    "AI_TAG_LOCAL_CAMPAIGN_PLAN_RESERVATION_SCHEMA_VERSION",
    "AI_TAG_REPOSITORY_SYNTHETIC_CAMPAIGN_CASE_SCHEMA_VERSION",
    "AtomicCampaignPlanBudgetLedger",
    "CampaignSmokePreflightError",
    "DEFAULT_CAMPAIGN_PLAN_MAX_OUTPUT_TOKENS",
    "DEFAULT_CAMPAIGN_PLAN_MAX_RESPONSE_BYTES",
    "DEFAULT_CAMPAIGN_PLAN_TIMEOUT_MS",
    "LocalCampaignEgressApproval",
    "LocalCampaignPlanReservation",
    "OneShotCampaignPlanApprovalVerifier",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT",
    "REPOSITORY_SYNTHETIC_CAMPAIGN_CASE",
    "RepositorySyntheticCampaignBundle",
    "RepositorySyntheticCampaignCaps",
    "RepositorySyntheticCampaignCase",
    "RepositorySyntheticCampaignRun",
    "build_campaign_inspection_summary",
    "build_local_campaign_egress_approval",
    "build_local_campaign_plan_reservations",
    "build_repository_synthetic_campaign_bundle",
    "load_campaign_run_summary",
    "main",
    "run_repository_synthetic_campaign",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
