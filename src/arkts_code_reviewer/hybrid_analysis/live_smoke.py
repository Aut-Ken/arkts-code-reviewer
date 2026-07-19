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
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, model_validator

from arkts_code_reviewer.hybrid_analysis._canonical import (
    FrozenModel,
    canonical_hash,
    canonical_json,
    identity_payload,
    seal_payload,
)
from arkts_code_reviewer.hybrid_analysis.builders import (
    AnalysisContextPolicy,
    build_ai_tag_model_view,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    DeepSeekOuterResponseDiagnostic,
    DeepSeekShadowHttpTransport,
    EnvironmentDeepSeekCredentialProvider,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import (
    AITagDispatchEnvelopeBuilder,
    VerifiedAITagDispatchEnvelope,
)
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
    verify_ai_tag_response_validation,
)
from arkts_code_reviewer.hybrid_analysis.models import (
    AITagAnalysisRequest,
    AITagModelView,
    ReviewUnitAnalysisCard,
    seal_review_unit_analysis_card,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagObservedProviderResponseReceiptV2,
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    AITagShadowExecutionObservationV2,
    build_ai_tag_shadow_dispatch_plan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import FullTaxonomyRequestBuilder
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowAuthorizationGate,
    AITagShadowRunArtifacts,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
)

AI_TAG_REPOSITORY_SMOKE_CASE_SCHEMA_VERSION = "ai-tag-repository-smoke-case-v1"
AI_TAG_LOCAL_EGRESS_APPROVAL_SCHEMA_VERSION = "ai-tag-local-egress-approval-v1"
AI_TAG_LOCAL_BUDGET_RESERVATION_SCHEMA_VERSION = "ai-tag-local-budget-reservation-v1"
AI_TAG_SHADOW_SMOKE_SUMMARY_SCHEMA_VERSION = "ai-tag-shadow-smoke-summary-v4"

REPOSITORY_SYNTHETIC_SMOKE_CASE = "repository-synthetic-timer-log-v1"
REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT = "YES_REPOSITORY_PROMPT_TAXONOMY_AND_SYNTHETIC_CODE"

DEFAULT_SMOKE_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_SMOKE_TIMEOUT_MS = 60_000
DEFAULT_SMOKE_MAX_RESPONSE_BYTES = 2_000_000

_REPOSITORY_SYNTHETIC_CODE = """startTimer(): void {
  const timerId = setInterval(() => {
    console.info('repository synthetic smoke tick');
  }, 1000);
  clearInterval(timerId);
}"""
_REPOSITORY_SYNTHETIC_CODE_SHA256 = (
    "sha256:58bab1c02761b3c16241c936e50bd6de638e8cf02c3c5da933f9fb9e9aa464d1"
)

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_CASE_ID = rf"^ai-tag-repository-smoke-case:sha256:{_HASH}$"
_PLAN_ID = rf"^ai-tag-shadow-plan:sha256:{_HASH}$"
_APPROVAL_ID = rf"^ai-egress-approval:sha256:{_HASH}$"
_RESERVATION_ID = rf"^ai-budget-reservation:sha256:{_HASH}$"
_CARD_ID = rf"^analysis-card:sha256:{_HASH}$"
_MODEL_VIEW_ID = rf"^ai-tag-model-view:sha256:{_HASH}$"
_REQUEST_ID = rf"^ai-tag-request:sha256:{_HASH}$"
_ENVELOPE_ID = rf"^ai-tag-dispatch-envelope:sha256:{_HASH}$"
_CATALOG_ID = rf"^ai-tag-contract-catalog:sha256:{_HASH}$"


def _hash_id(prefix: str, purpose: str) -> str:
    return canonical_hash(prefix, {"case": REPOSITORY_SYNTHETIC_SMOKE_CASE, "purpose": purpose})


def _fact_set(*, calls_and_apis: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "apis": calls_and_apis,
        "components": (),
        "decorators": (),
        "attributes": (),
        "symbols": (),
        "syntax": (),
        "calls": calls_and_apis,
        "import_bindings": (),
        "import_uses": (),
        "field_reads": (),
        "field_writes": (),
        "string_literals": (),
        "resource_references": (),
    }


class _RepositorySyntheticSmokeCasePayload(FrozenModel):
    schema_version: Literal["ai-tag-repository-smoke-case-v1"]
    case_name: Literal["repository-synthetic-timer-log-v1"]
    origin: Literal["repository_authored_synthetic"]
    data_classification: Literal["repository_contained_synthetic_no_user_code"]
    source_policy: Literal["closed_package_assets_no_external_input"]
    outbound_asset_scope: Literal["repository_prompt_taxonomy_and_synthetic_code"]
    code_sha256: Annotated[str, Field(pattern=_SHA256)]
    prompt_hash: Annotated[str, Field(pattern=_SHA256)]
    catalog_fingerprint: Annotated[str, Field(pattern=_CATALOG_ID)]
    card_id: Annotated[str, Field(pattern=_CARD_ID)]
    model_view_id: Annotated[str, Field(pattern=_MODEL_VIEW_ID)]
    request_id: Annotated[str, Field(pattern=_REQUEST_ID)]
    envelope_id: Annotated[str, Field(pattern=_ENVELOPE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    execution_scope: Literal["shadow_only_no_hybrid_no_retrieval"]
    qualification: Literal["connectivity_and_contract_smoke_not_tag_truth"]


class RepositorySyntheticSmokeCase(_RepositorySyntheticSmokeCasePayload):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-tag-repository-smoke-case",
            identity_payload(self, "case_id"),
        )
        if self.case_id != expected:
            raise ValueError("repository-contained synthetic smoke case ID does not match")
        return self


class _LocalExactBodyEgressApprovalPayload(FrozenModel):
    schema_version: Literal["ai-tag-local-egress-approval-v1"]
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    endpoint_url: Literal["https://api.deepseek.com/chat/completions"]
    model: Literal["deepseek-v4-pro"]
    max_attempts: Literal[1]
    operator_acknowledgement: Literal["YES_REPOSITORY_PROMPT_TAXONOMY_AND_SYNTHETIC_CODE"]
    outbound_asset_scope: Literal["repository_prompt_taxonomy_and_synthetic_code"]
    approval_scope: Literal["one_process_exact_repository_synthetic_body"]
    qualification: Literal["local_operator_control_not_deployment_compliance_approval"]


class LocalExactBodyEgressApproval(_LocalExactBodyEgressApprovalPayload):
    approval_id: Annotated[str, Field(pattern=_APPROVAL_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-egress-approval",
            identity_payload(self, "approval_id"),
        )
        if self.approval_id != expected:
            raise ValueError("local egress approval ID does not match its contents")
        return self


class _LocalOneAttemptBudgetReservationPayload(FrozenModel):
    schema_version: Literal["ai-tag-local-budget-reservation-v1"]
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    max_output_tokens: Annotated[int, Field(ge=256, le=16_384)]
    wall_clock_timeout_ms: Annotated[int, Field(ge=1_000, le=120_000)]
    max_response_bytes: Annotated[int, Field(ge=1_024, le=8_000_000)]
    max_attempts: Literal[1]
    reservation_scope: Literal["one_local_fixed_body_attempt"]
    replay_guard: Literal["atomic_local_marker"]
    qualification: Literal["local_attempt_cap_not_currency_or_provider_budget"]


class LocalOneAttemptBudgetReservation(_LocalOneAttemptBudgetReservationPayload):
    reservation_id: Annotated[str, Field(pattern=_RESERVATION_ID)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = canonical_hash(
            "ai-budget-reservation",
            identity_payload(self, "reservation_id"),
        )
        if self.reservation_id != expected:
            raise ValueError("local budget reservation ID does not match its contents")
        return self


@dataclass(frozen=True)
class RepositorySyntheticSmokeBundle:
    manifest: RepositorySyntheticSmokeCase
    card: ReviewUnitAnalysisCard
    model_view: AITagModelView
    request: AITagAnalysisRequest
    envelope: VerifiedAITagDispatchEnvelope
    plan: AITagShadowDispatchPlan
    context_policy: AnalysisContextPolicy
    trusted_plan_inputs: AITagShadowTrustedPlanInputs


@dataclass(frozen=True)
class RepositorySyntheticSmokeRun:
    manifest: RepositorySyntheticSmokeCase
    approval: LocalExactBodyEgressApproval
    reservation: LocalOneAttemptBudgetReservation
    claims: AITagShadowDispatchClaims
    artifacts: AITagShadowRunArtifacts


class CredentialProviderFactory(Protocol):
    def __call__(self) -> DeepSeekCredentialProvider: ...


class SmokePreflightError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _assert_repository_fixture_identity() -> None:
    actual = "sha256:" + hashlib.sha256(_REPOSITORY_SYNTHETIC_CODE.encode("utf-8")).hexdigest()
    if actual != _REPOSITORY_SYNTHETIC_CODE_SHA256:
        raise SmokePreflightError("repository_fixture_identity_mismatch")


def _build_repository_synthetic_card(
    *,
    context_policy: AnalysisContextPolicy,
    feature_config_fingerprint: str,
) -> ReviewUnitAnalysisCard:
    calls_and_apis = tuple(sorted(("clearInterval", "console.info", "setInterval")))
    return seal_review_unit_analysis_card(
        {
            "schema_version": "review-unit-analysis-card-v1",
            "unit_id": "repository-synthetic-smoke:TimerProbe.startTimer",
            "source_ref_id": _hash_id("code-source", "source"),
            "feature_profile_id": _hash_id("feature-profile", "profile"),
            "feature_routing_id": _hash_id("feature-routing", "routing"),
            "context_plan_id": _hash_id("context-plan", "context"),
            "source_role": "head",
            "unit_kind": "method",
            "unit_symbol": "TimerProbe.startTimer",
            "owner_summary": {
                "resolution": "resolved",
                "unit_owner": {
                    "kind": "declaration",
                    "ref_id": _hash_id("declaration", "method-owner"),
                    "owner_kind": "method",
                    "qualified_name": "TimerProbe.startTimer",
                    "quality": "exact",
                },
                "enclosing_owner": {
                    "kind": "declaration",
                    "ref_id": _hash_id("declaration", "struct-owner"),
                    "owner_kind": "struct",
                    "qualified_name": "TimerProbe",
                    "quality": "exact",
                },
                "owner_roles": (),
                "diagnostics": (),
            },
            "code": {
                "mode": "full_unit",
                "text": _REPOSITORY_SYNTHETIC_CODE,
                "line_start": 1,
                "line_end": 6,
                "changed_line_numbers": (1, 2, 3, 4, 5, 6),
                "truncated": False,
            },
            "change_atom_ids": (_hash_id("change-atom", "direct-change"),),
            "exact_occurrence_ids": (),
            "owner_context_occurrence_ids": (),
            "owner_context_declaration_ids": (),
            "unit_fact_diagnostics": (),
            "facts": {
                "unit_exact": _fact_set(calls_and_apis=calls_and_apis),
                "file_hints": _fact_set(),
            },
            "static_tags": {"exact": (), "routing": (), "matches": ()},
            "quality": {
                "parser_layer": "L1",
                "error_nodes": 0,
                "missing_nodes": 0,
                "context_degraded": False,
                "unit_owner_unresolved": False,
            },
            "available_context_refs": (),
            "code_token_budget": context_policy.code_token_budget,
            "feature_config_fingerprint": feature_config_fingerprint,
            "context_policy_fingerprint": context_policy.fingerprint,
        }
    )


def build_repository_synthetic_smoke_bundle(
    *,
    max_output_tokens: int = DEFAULT_SMOKE_MAX_OUTPUT_TOKENS,
    timeout_ms: int = DEFAULT_SMOKE_TIMEOUT_MS,
    max_response_bytes: int = DEFAULT_SMOKE_MAX_RESPONSE_BYTES,
) -> RepositorySyntheticSmokeBundle:
    """Rebuild the only repository-contained asset combination eligible for smoke."""

    _assert_repository_fixture_identity()
    context_policy = AnalysisContextPolicy(
        builder_version="analysis-card-builder-v2-provider-egress",
        redaction_policy="none_requires_exact_body_runtime_approval",
    )
    request_builder = FullTaxonomyRequestBuilder.default()
    card = _build_repository_synthetic_card(
        context_policy=context_policy,
        feature_config_fingerprint=request_builder.feature_config.fingerprint,
    )
    model_view = build_ai_tag_model_view(card=card)
    request = request_builder.build(card=card, model_view=model_view)
    envelope = AITagDispatchEnvelopeBuilder(request_builder=request_builder).build(
        card=card,
        model_view=model_view,
        request=request,
    )
    plan = build_ai_tag_shadow_dispatch_plan(
        envelope=envelope,
        card=card,
        context_policy=context_policy,
        max_output_tokens=max_output_tokens,
        timeout_ms=timeout_ms,
        max_response_bytes=max_response_bytes,
    )
    trusted_inputs = AITagShadowTrustedPlanInputs(
        envelope=envelope,
        card=card,
        context_policy=context_policy,
        max_output_tokens=max_output_tokens,
        wall_clock_timeout_ms=timeout_ms,
        max_response_bytes=max_response_bytes,
    )
    manifest = seal_payload(
        {
            "schema_version": AI_TAG_REPOSITORY_SMOKE_CASE_SCHEMA_VERSION,
            "case_name": REPOSITORY_SYNTHETIC_SMOKE_CASE,
            "origin": "repository_authored_synthetic",
            "data_classification": "repository_contained_synthetic_no_user_code",
            "source_policy": "closed_package_assets_no_external_input",
            "outbound_asset_scope": "repository_prompt_taxonomy_and_synthetic_code",
            "code_sha256": _REPOSITORY_SYNTHETIC_CODE_SHA256,
            "prompt_hash": request_builder.prompt.prompt_hash,
            "catalog_fingerprint": request_builder.catalog.catalog_fingerprint,
            "card_id": card.card_id,
            "model_view_id": model_view.model_view_id,
            "request_id": request.request_id,
            "envelope_id": envelope.envelope_id,
            "plan_id": plan.plan_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "execution_scope": "shadow_only_no_hybrid_no_retrieval",
            "qualification": "connectivity_and_contract_smoke_not_tag_truth",
        },
        payload_type=_RepositorySyntheticSmokeCasePayload,
        sealed_type=RepositorySyntheticSmokeCase,
        identity_field="case_id",
        identity_prefix="ai-tag-repository-smoke-case",
        context="Repository Synthetic Smoke Case",
    )
    return RepositorySyntheticSmokeBundle(
        manifest=manifest,
        card=card,
        model_view=model_view,
        request=request,
        envelope=envelope,
        plan=plan,
        context_policy=context_policy,
        trusted_plan_inputs=trusted_inputs,
    )


def _require_canonical_repository_bundle(
    bundle: RepositorySyntheticSmokeBundle,
) -> RepositorySyntheticSmokeBundle:
    if not isinstance(bundle, RepositorySyntheticSmokeBundle):
        raise SmokePreflightError("repository_smoke_bundle_not_trusted")
    try:
        expected = build_repository_synthetic_smoke_bundle(
            max_output_tokens=bundle.plan.wire_payload.max_tokens,
            timeout_ms=bundle.plan.wall_clock_timeout_ms,
            max_response_bytes=bundle.plan.max_response_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SmokePreflightError("repository_smoke_bundle_not_trusted") from exc
    if bundle != expected:
        raise SmokePreflightError("repository_smoke_bundle_not_trusted")
    return expected


def _manifest_and_plan_are_canonical(
    *,
    manifest: RepositorySyntheticSmokeCase,
    plan: AITagShadowDispatchPlan,
) -> bool:
    try:
        expected = build_repository_synthetic_smoke_bundle(
            max_output_tokens=plan.wire_payload.max_tokens,
            timeout_ms=plan.wall_clock_timeout_ms,
            max_response_bytes=plan.max_response_bytes,
        )
    except (AttributeError, TypeError, ValueError, SmokePreflightError):
        return False
    return manifest == expected.manifest and plan == expected.plan


def build_local_exact_body_approval(
    bundle: RepositorySyntheticSmokeBundle,
    *,
    approved_plan_id: str,
    approved_wire_body_sha256: str,
    acknowledgement: str,
) -> LocalExactBodyEgressApproval:
    bundle = _require_canonical_repository_bundle(bundle)
    plan = bundle.plan
    if approved_plan_id != plan.plan_id:
        raise SmokePreflightError("approved_plan_id_mismatch")
    if approved_wire_body_sha256 != plan.wire_body_sha256:
        raise SmokePreflightError("approved_wire_body_sha256_mismatch")
    if acknowledgement != REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT:
        raise SmokePreflightError("repository_synthetic_acknowledgement_missing")
    return seal_payload(
        {
            "schema_version": AI_TAG_LOCAL_EGRESS_APPROVAL_SCHEMA_VERSION,
            "case_id": bundle.manifest.case_id,
            "plan_id": plan.plan_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "endpoint_url": plan.endpoint_url,
            "model": plan.wire_payload.model,
            "max_attempts": plan.max_attempts,
            "operator_acknowledgement": REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
            "outbound_asset_scope": "repository_prompt_taxonomy_and_synthetic_code",
            "approval_scope": "one_process_exact_repository_synthetic_body",
            "qualification": "local_operator_control_not_deployment_compliance_approval",
        },
        payload_type=_LocalExactBodyEgressApprovalPayload,
        sealed_type=LocalExactBodyEgressApproval,
        identity_field="approval_id",
        identity_prefix="ai-egress-approval",
        context="Local Exact Body Egress Approval",
    )


def build_local_one_attempt_reservation(
    bundle: RepositorySyntheticSmokeBundle,
    *,
    reserved_max_output_tokens: int,
) -> LocalOneAttemptBudgetReservation:
    bundle = _require_canonical_repository_bundle(bundle)
    plan = bundle.plan
    if reserved_max_output_tokens != plan.wire_payload.max_tokens:
        raise SmokePreflightError("reserved_max_output_tokens_mismatch")
    return seal_payload(
        {
            "schema_version": AI_TAG_LOCAL_BUDGET_RESERVATION_SCHEMA_VERSION,
            "case_id": bundle.manifest.case_id,
            "plan_id": plan.plan_id,
            "wire_body_sha256": plan.wire_body_sha256,
            "max_output_tokens": plan.wire_payload.max_tokens,
            "wall_clock_timeout_ms": plan.wall_clock_timeout_ms,
            "max_response_bytes": plan.max_response_bytes,
            "max_attempts": plan.max_attempts,
            "reservation_scope": "one_local_fixed_body_attempt",
            "replay_guard": "atomic_local_marker",
            "qualification": "local_attempt_cap_not_currency_or_provider_budget",
        },
        payload_type=_LocalOneAttemptBudgetReservationPayload,
        sealed_type=LocalOneAttemptBudgetReservation,
        identity_field="reservation_id",
        identity_prefix="ai-budget-reservation",
        context="Local One Attempt Budget Reservation",
    )


class OneShotExactBodyApprovalVerifier:
    """Consume one process-local approval bound to one package fixture Plan."""

    def __init__(
        self,
        *,
        manifest: RepositorySyntheticSmokeCase,
        approval: LocalExactBodyEgressApproval,
    ) -> None:
        self._manifest = RepositorySyntheticSmokeCase.model_validate(
            manifest.model_dump(mode="json")
        )
        self._approval = LocalExactBodyEgressApproval.model_validate(
            approval.model_dump(mode="json")
        )
        self._consumed = False
        self._lock = threading.Lock()

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        approval = self._approval
        if (
            not _manifest_and_plan_are_canonical(
                manifest=self._manifest,
                plan=plan,
            )
            or approval_id != approval.approval_id
            or approval.case_id != self._manifest.case_id
            or approval.plan_id != plan.plan_id
            or approval.wire_body_sha256 != plan.wire_body_sha256
            or approval.endpoint_url != plan.endpoint_url
            or approval.model != plan.wire_payload.model
            or approval.max_attempts != plan.max_attempts
        ):
            raise AITagShadowAuthorizationError("egress_not_approved")
        with self._lock:
            if self._consumed:
                raise AITagShadowAuthorizationError("egress_not_approved")
            self._consumed = True


class AtomicLocalOneAttemptBudgetLedger:
    """A local replay guard; it is neither a cost ledger nor durable authority."""

    def __init__(
        self,
        *,
        manifest: RepositorySyntheticSmokeCase,
        reservation: LocalOneAttemptBudgetReservation,
        state_dir: Path,
    ) -> None:
        self._manifest = RepositorySyntheticSmokeCase.model_validate(
            manifest.model_dump(mode="json")
        )
        self._reservation = LocalOneAttemptBudgetReservation.model_validate(
            reservation.model_dump(mode="json")
        )
        self._state_dir = Path(state_dir)
        self._lock = threading.Lock()

    @property
    def marker_path(self) -> Path:
        digest = self._reservation.reservation_id.rsplit(":", 1)[-1]
        return self._state_dir / f"{digest}.consumed.json"

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        reservation = self._reservation
        if (
            not _manifest_and_plan_are_canonical(
                manifest=self._manifest,
                plan=plan,
            )
            or reservation_id != reservation.reservation_id
            or reservation.case_id != self._manifest.case_id
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
        try:
            self._ensure_state_dir()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.marker_path, flags, 0o600)
        except (FileExistsError, OSError, ValueError):
            raise AITagShadowAuthorizationError("budget_not_reserved") from None
        marker = canonical_json(
            {
                "schema_version": "ai-tag-local-attempt-consumption-v1",
                "case_id": self._manifest.case_id,
                "plan_id": plan.plan_id,
                "wire_body_sha256": plan.wire_body_sha256,
                "reservation_id": self._reservation.reservation_id,
                "qualification": "local_replay_guard_not_provider_or_cost_evidence",
            }
        ).encode("utf-8")
        try:
            os.write(descriptor, marker)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _ensure_state_dir(self) -> None:
        state_dir = self._state_dir
        if state_dir.exists():
            if state_dir.is_symlink() or not state_dir.is_dir():
                raise ValueError("unsafe smoke state directory")
        else:
            parent = state_dir.parent
            if not parent.is_dir() or parent.is_symlink():
                raise ValueError("smoke state parent must be an existing real directory")
            os.mkdir(state_dir, mode=0o700)
        metadata = os.stat(state_dir, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("smoke state directory must not be group/world accessible")


def _build_claims(
    *,
    bundle: RepositorySyntheticSmokeBundle,
    approval: LocalExactBodyEgressApproval,
    reservation: LocalOneAttemptBudgetReservation,
    credential_scope_id: str,
) -> AITagShadowDispatchClaims:
    trust_domain_id = canonical_hash(
        "ai-shadow-trust-domain",
        {
            "scope": "local-repository-synthetic-smoke-v1",
            "case_id": bundle.manifest.case_id,
        },
    )
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": bundle.plan.plan_id,
            "trust_domain_id": trust_domain_id,
            "egress_approval_id": approval.approval_id,
            "budget_reservation_id": reservation.reservation_id,
            "credential_scope_id": credential_scope_id,
            "egress_scope": "exact_wire_body_sha256",
            "budget_scope": "one_attempt_worst_case_reserved",
            "qualification": "references_require_runtime_verification",
        }
    )


def run_repository_synthetic_smoke(
    *,
    bundle: RepositorySyntheticSmokeBundle,
    approved_plan_id: str,
    approved_wire_body_sha256: str,
    reserved_max_output_tokens: int,
    acknowledgement: str,
    state_dir: Path,
    credential_provider: DeepSeekCredentialProvider,
    transport: DeepSeekShadowHttpTransport | None = None,
) -> RepositorySyntheticSmokeRun:
    bundle = _require_canonical_repository_bundle(bundle)
    approval = build_local_exact_body_approval(
        bundle,
        approved_plan_id=approved_plan_id,
        approved_wire_body_sha256=approved_wire_body_sha256,
        acknowledgement=acknowledgement,
    )
    reservation = build_local_one_attempt_reservation(
        bundle,
        reserved_max_output_tokens=reserved_max_output_tokens,
    )
    claims = _build_claims(
        bundle=bundle,
        approval=approval,
        reservation=reservation,
        credential_scope_id=credential_provider.credential_scope_id,
    )
    gate = AITagShadowAuthorizationGate(
        trust_domain_id=claims.trust_domain_id,
        credential_provider=credential_provider,
        trusted_plan_inputs=bundle.trusted_plan_inputs,
        egress_verifier=OneShotExactBodyApprovalVerifier(
            manifest=bundle.manifest,
            approval=approval,
        ),
        budget_ledger=AtomicLocalOneAttemptBudgetLedger(
            manifest=bundle.manifest,
            reservation=reservation,
            state_dir=state_dir,
        ),
    )
    capability = gate.authorize(plan=bundle.plan, claims=claims)
    runner = DeepSeekShadowRunner(gate=gate, transport=transport)
    artifacts = runner.run(
        plan=bundle.plan,
        claims=claims,
        capability=capability,
        envelope=bundle.envelope,
    )
    return RepositorySyntheticSmokeRun(
        manifest=bundle.manifest,
        approval=approval,
        reservation=reservation,
        claims=claims,
        artifacts=artifacts,
    )


def build_inspection_summary(bundle: RepositorySyntheticSmokeBundle) -> dict[str, object]:
    plan = bundle.plan
    return {
        "schema_version": AI_TAG_SHADOW_SMOKE_SUMMARY_SCHEMA_VERSION,
        "mode": "inspect_only",
        "network_attempted": False,
        "case_id": bundle.manifest.case_id,
        "case_name": bundle.manifest.case_name,
        "data_classification": bundle.manifest.data_classification,
        "outbound_asset_scope": bundle.manifest.outbound_asset_scope,
        "prompt_hash": bundle.manifest.prompt_hash,
        "catalog_fingerprint": bundle.manifest.catalog_fingerprint,
        "card_id": bundle.card.card_id,
        "model_view_id": bundle.model_view.model_view_id,
        "request_id": bundle.request.request_id,
        "envelope_id": plan.envelope_id,
        "plan_id": plan.plan_id,
        "endpoint_url": plan.endpoint_url,
        "model": plan.wire_payload.model,
        "wire_body_sha256": plan.wire_body_sha256,
        "wire_body_size_bytes": len(plan.wire_body_json.encode("utf-8")),
        "max_output_tokens": plan.wire_payload.max_tokens,
        "wall_clock_timeout_ms": plan.wall_clock_timeout_ms,
        "max_response_bytes": plan.max_response_bytes,
        "max_attempts": plan.max_attempts,
        "required_acknowledgement": REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
        "ignored_usage_extension_count": None,
        "validated_tag_decisions": [],
        "qualification": "inspect_only_connectivity_contract_smoke_not_tag_truth",
    }


def _project_validated_tag_decisions(
    run: RepositorySyntheticSmokeRun,
    *,
    bundle: RepositorySyntheticSmokeBundle,
) -> tuple[
    list[dict[str, str]],
    AITagObservedProviderResponseReceiptV2 | None,
    AITagResponseValidation | None,
    AITagShadowExecutionObservationV2,
]:
    try:
        bundle = _require_canonical_repository_bundle(bundle)
        artifacts = run.artifacts
        observation = AITagShadowExecutionObservationV2.model_validate(
            artifacts.observation.model_dump(mode="json")
        )
        validation = (
            None
            if artifacts.response_validation is None
            else AITagResponseValidation.model_validate(
                artifacts.response_validation.model_dump(mode="json")
            )
        )
        provider_receipt = (
            None
            if artifacts.provider_response_receipt is None
            else AITagObservedProviderResponseReceiptV2.model_validate(
                artifacts.provider_response_receipt.model_dump(mode="json")
            )
        )
        if validation is not None:
            verify_ai_tag_response_validation(validation, bundle.envelope)
    except (AttributeError, SmokePreflightError, TypeError, ValueError):
        raise ValueError("invalid validated smoke Tag decision projection inputs") from None
    if run.manifest != bundle.manifest:
        raise ValueError("validated smoke Tag decision manifests differ")
    if (
        observation.plan_id != bundle.plan.plan_id
        or observation.claims_id != run.claims.claims_id
        or observation.attempt_receipt_id != artifacts.attempt_receipt.receipt_id
    ):
        raise ValueError("validated smoke Tag decision response graph differs")
    if provider_receipt is None and validation is None:
        if (
            observation.provider_response_receipt_id is not None
            or observation.response_validation_id is not None
            or observation.status == "valid_shape"
        ):
            raise ValueError("validated smoke Tag decision response graph is incomplete")
        return [], None, None, observation
    if provider_receipt is None or validation is None:
        raise ValueError("validated smoke Tag decision response graph is incomplete")
    if (
        artifacts.outer_response_diagnostic is not None
        or observation.provider_response_receipt_id != provider_receipt.receipt_id
        or observation.response_validation_id != validation.validation_id
        or provider_receipt.plan_id != bundle.plan.plan_id
        or provider_receipt.attempt_receipt_id != artifacts.attempt_receipt.receipt_id
        or provider_receipt.response_body_sha256 != artifacts.attempt_receipt.response_body_sha256
        or provider_receipt.response_body_size_bytes
        != artifacts.attempt_receipt.response_body_size_bytes
        or provider_receipt.content_sha256 != validation.raw_content_sha256
        or provider_receipt.model != validation.model
        or (provider_receipt.system_fingerprint or "not_reported") != validation.system_fingerprint
        or provider_receipt.finish_reason != validation.finish_reason
    ):
        raise ValueError("validated smoke Tag decision response graph differs")
    validation_is_valid = validation is not None and validation.status == "valid_shape"
    observation_is_valid = observation.status == "valid_shape"
    if not validation_is_valid and not observation_is_valid:
        if validation.status != "invalid_output" or observation.status != "invalid_output":
            raise ValueError("validated smoke Tag decision statuses differ")
        return [], provider_receipt, validation, observation
    if not validation_is_valid or not observation_is_valid:
        raise ValueError("validated smoke Tag decision statuses differ")
    expected_tag_ids = tuple(contract.tag_id for contract in bundle.request.tag_contract_views)
    actual_tag_ids = tuple(judgment.tag_id for judgment in validation.judgments)
    if actual_tag_ids != expected_tag_ids:
        raise ValueError("validated smoke Tag decisions differ from the closed request")
    return (
        [
            {"tag_id": judgment.tag_id, "decision": judgment.decision}
            for judgment in validation.judgments
        ],
        provider_receipt,
        validation,
        observation,
    )


def _build_run_summary(
    run: RepositorySyntheticSmokeRun,
    *,
    bundle: RepositorySyntheticSmokeBundle,
) -> dict[str, object]:
    """Format a Runner-verified run; this is not an artifact verification API."""

    artifacts = run.artifacts
    attempt = artifacts.attempt_receipt
    (
        validated_tag_decisions,
        provider_receipt,
        validation,
        observation,
    ) = _project_validated_tag_decisions(run, bundle=bundle)
    try:
        outer_diagnostic = (
            None
            if artifacts.outer_response_diagnostic is None
            else DeepSeekOuterResponseDiagnostic.model_validate(
                artifacts.outer_response_diagnostic.model_dump(mode="json")
            )
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid smoke outer diagnostic") from None
    decision_counts: dict[str, int] = {}
    usage: Mapping[str, object] | None = None
    if validation is not None:
        usage = validation.usage.model_dump(mode="json")
    for item in validated_tag_decisions:
        decision = item["decision"]
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    return {
        "schema_version": AI_TAG_SHADOW_SMOKE_SUMMARY_SCHEMA_VERSION,
        "mode": "live_shadow_attempt",
        "network_attempted": (
            True if attempt.network_observation == "observed_by_fixed_httpx_transport" else None
        ),
        "case_id": run.manifest.case_id,
        "case_name": run.manifest.case_name,
        "data_classification": run.manifest.data_classification,
        "outbound_asset_scope": run.manifest.outbound_asset_scope,
        "prompt_hash": run.manifest.prompt_hash,
        "catalog_fingerprint": run.manifest.catalog_fingerprint,
        "plan_id": attempt.plan_id,
        "wire_body_sha256": attempt.wire_body_sha256,
        "approval_id": run.approval.approval_id,
        "reservation_id": run.reservation.reservation_id,
        "claims_id": run.claims.claims_id,
        "attempt_receipt_id": attempt.receipt_id,
        "transport_evidence": attempt.transport_evidence,
        "network_observation": attempt.network_observation,
        "transport_status": attempt.transport_status,
        "http_status": attempt.http_status,
        "latency_ms": attempt.latency_ms,
        "response_body_sha256": attempt.response_body_sha256,
        "response_body_size_bytes": attempt.response_body_size_bytes,
        "provider_response_receipt_id": (
            None if provider_receipt is None else provider_receipt.receipt_id
        ),
        "response_validation_id": (None if validation is None else validation.validation_id),
        "outer_diagnostic_id": (
            None if outer_diagnostic is None else outer_diagnostic.diagnostic_id
        ),
        "outer_diagnostic_parser_contract_version": (
            None if outer_diagnostic is None else outer_diagnostic.parser_contract_version
        ),
        "outer_diagnostic_stage": (None if outer_diagnostic is None else outer_diagnostic.stage),
        "outer_diagnostic_error_type": (
            None if outer_diagnostic is None else outer_diagnostic.error_type
        ),
        "outer_diagnostic_field_path": (
            None if outer_diagnostic is None else list(outer_diagnostic.field_path)
        ),
        "observation_id": observation.observation_id,
        "status": observation.status,
        "reason_code": observation.reason_code,
        "ignored_usage_extension_count": (
            None if provider_receipt is None else provider_receipt.ignored_usage_extension_count
        ),
        "judgment_count": len(validated_tag_decisions),
        "decision_counts": decision_counts,
        "validated_tag_decisions": validated_tag_decisions,
        "usage": usage,
        "raw_response_retained": False,
        "rebuild_scope": "verified_in_process_only",
        "qualification": "local_unattested_shadow_smoke_not_formal_or_quality_evidence",
    }


def _safe_error_summary(*, code: str, attempted: bool | None) -> dict[str, object]:
    return {
        "schema_version": AI_TAG_SHADOW_SMOKE_SUMMARY_SCHEMA_VERSION,
        "mode": "live_shadow_preflight" if attempted is False else "live_shadow_attempt",
        "network_attempted": attempted,
        "error_code": code,
        "ignored_usage_extension_count": None,
        "validated_tag_decisions": [],
        "qualification": "local_smoke_error_not_formal_or_quality_evidence",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or explicitly run the package-contained repository-synthetic "
            "DeepSeek shadow smoke case."
        )
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--approve-plan-id")
    parser.add_argument("--approve-body-sha256")
    parser.add_argument("--reserve-max-output-tokens", type=int)
    parser.add_argument("--acknowledge-repository-assets-and-synthetic-code")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_SMOKE_MAX_OUTPUT_TOKENS,
    )
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_SMOKE_TIMEOUT_MS)
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=DEFAULT_SMOKE_MAX_RESPONSE_BYTES,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_provider_factory: CredentialProviderFactory = (
        EnvironmentDeepSeekCredentialProvider
    ),
    test_transport: DeepSeekShadowHttpTransport | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = build_repository_synthetic_smoke_bundle(
            max_output_tokens=args.max_output_tokens,
            timeout_ms=args.timeout_ms,
            max_response_bytes=args.max_response_bytes,
        )
    except (SmokePreflightError, TypeError, ValueError):
        print(canonical_json(_safe_error_summary(code="fixture_or_plan_invalid", attempted=False)))
        return 2
    if not args.execute_live:
        print(canonical_json(build_inspection_summary(bundle)))
        return 0
    required = (
        args.approve_plan_id,
        args.approve_body_sha256,
        args.reserve_max_output_tokens,
        args.acknowledge_repository_assets_and_synthetic_code,
        args.state_dir,
    )
    if any(value is None for value in required):
        print(canonical_json(_safe_error_summary(code="live_controls_incomplete", attempted=False)))
        return 2
    try:
        credential_provider = credential_provider_factory()
        run = run_repository_synthetic_smoke(
            bundle=bundle,
            approved_plan_id=args.approve_plan_id,
            approved_wire_body_sha256=args.approve_body_sha256,
            reserved_max_output_tokens=args.reserve_max_output_tokens,
            acknowledgement=args.acknowledge_repository_assets_and_synthetic_code,
            state_dir=args.state_dir,
            credential_provider=credential_provider,
            transport=test_transport,
        )
    except SmokePreflightError as exc:
        print(canonical_json(_safe_error_summary(code=exc.code, attempted=False)))
        return 2
    except AITagShadowAuthorizationError as exc:
        print(canonical_json(_safe_error_summary(code=exc.reason_code, attempted=False)))
        return 2
    except Exception:
        print(canonical_json(_safe_error_summary(code="smoke_runtime_error", attempted=None)))
        return 3
    try:
        summary = _build_run_summary(run, bundle=bundle)
    except (TypeError, ValueError):
        print(canonical_json(_safe_error_summary(code="smoke_summary_invalid", attempted=True)))
        return 3
    print(canonical_json(summary))
    return 0 if run.artifacts.observation.status == "valid_shape" else 3


__all__ = [
    "AI_TAG_LOCAL_BUDGET_RESERVATION_SCHEMA_VERSION",
    "AI_TAG_LOCAL_EGRESS_APPROVAL_SCHEMA_VERSION",
    "AI_TAG_REPOSITORY_SMOKE_CASE_SCHEMA_VERSION",
    "AI_TAG_SHADOW_SMOKE_SUMMARY_SCHEMA_VERSION",
    "AtomicLocalOneAttemptBudgetLedger",
    "DEFAULT_SMOKE_MAX_OUTPUT_TOKENS",
    "DEFAULT_SMOKE_MAX_RESPONSE_BYTES",
    "DEFAULT_SMOKE_TIMEOUT_MS",
    "LocalExactBodyEgressApproval",
    "LocalOneAttemptBudgetReservation",
    "OneShotExactBodyApprovalVerifier",
    "REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT",
    "REPOSITORY_SYNTHETIC_SMOKE_CASE",
    "RepositorySyntheticSmokeBundle",
    "RepositorySyntheticSmokeCase",
    "RepositorySyntheticSmokeRun",
    "SmokePreflightError",
    "build_inspection_summary",
    "build_local_exact_body_approval",
    "build_local_one_attempt_reservation",
    "build_repository_synthetic_smoke_bundle",
    "main",
    "run_repository_synthetic_smoke",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
