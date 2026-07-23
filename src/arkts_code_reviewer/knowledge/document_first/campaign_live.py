from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    DeepSeekCredentialUnavailableError,
)
from arkts_code_reviewer.knowledge.document_first._canonical import (
    FrozenModel,
    canonical_hash,
    load_json_model,
)
from arkts_code_reviewer.knowledge.document_first.campaign import (
    DocumentCardCampaignBundle,
    DocumentCardCampaignPlanBundle,
    verify_document_card_campaign,
)
from arkts_code_reviewer.knowledge.document_first.live_smoke import (
    DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
    DocumentCardRunArtifacts,
    DocumentCardRunStatus,
    DocumentCardSmokeBundle,
    DocumentCardSmokeError,
    DocumentCardTransport,
    _verify_run_artifacts,
    _write_or_verify,
    run_document_card_live_once,
)

DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT = "YES_DEEPSEEK_DOCUMENT_CARD_EXACT_CAMPAIGN"
DOCUMENT_CARD_CAMPAIGN_LIVE_RECEIPT_SCHEMA_VERSION: Literal[
    "document-card-campaign-live-receipt-v1"
] = "document-card-campaign-live-receipt-v1"

_HASH = r"[0-9a-f]{64}"
_SHA256 = rf"^sha256:{_HASH}$"
_PLAN_ID = rf"^document-card-plan:sha256:{_HASH}$"
_CARD_ID = rf"^document-card:sha256:{_HASH}$"
_SINGLE_RECEIPT_ID = rf"^document-card-live-receipt:sha256:{_HASH}$"
_CAMPAIGN_ID = rf"^document-card-campaign:sha256:{_HASH}$"
_PLAN_SET_DIGEST = rf"^document-card-campaign-plan-set:sha256:{_HASH}$"
_CAMPAIGN_RECEIPT_ID = rf"^document-card-campaign-live-receipt:sha256:{_HASH}$"

DocumentCardFailureCode = Literal[
    "transport_failed",
    "http_status_not_success",
    "outer_response_invalid",
    "finish_reason_not_stop",
    "usage_exceeds_reserved_output",
    "document_card_draft_invalid",
]


class DocumentCardCampaignLiveError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


class DocumentCardCampaignLiveItem(FrozenModel):
    ordinal: Annotated[int, Field(ge=0)]
    plan_id: Annotated[str, Field(pattern=_PLAN_ID)]
    wire_body_sha256: Annotated[str, Field(pattern=_SHA256)]
    receipt_id: Annotated[str, Field(pattern=_SINGLE_RECEIPT_ID)]
    status: DocumentCardRunStatus
    failure_code: DocumentCardFailureCode | None
    card_id: Annotated[str | None, Field(pattern=_CARD_ID)]
    latency_ms: Annotated[int, Field(ge=0)]
    response_body_size_bytes: Annotated[int | None, Field(ge=0)]
    prompt_tokens: Annotated[int | None, Field(ge=0)]
    completion_tokens: Annotated[int | None, Field(ge=0)]
    total_tokens: Annotated[int | None, Field(ge=0)]

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        token_fields = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        if any(value is None for value in token_fields) and any(
            value is not None for value in token_fields
        ):
            raise ValueError("campaign item token fields must appear together")
        if (
            self.prompt_tokens is not None
            and self.completion_tokens is not None
            and self.total_tokens != self.prompt_tokens + self.completion_tokens
        ):
            raise ValueError("campaign item token total does not match")
        if self.status == "valid_card":
            if self.card_id is None or self.failure_code is not None:
                raise ValueError("valid campaign item is incomplete")
        elif self.card_id is not None or self.failure_code is None:
            raise ValueError("failed campaign item has an invalid result shape")
        return self


class _DocumentCardCampaignLiveReceiptFields(FrozenModel):
    schema_version: Literal["document-card-campaign-live-receipt-v1"]
    campaign_id: Annotated[str, Field(pattern=_CAMPAIGN_ID)]
    plan_set_digest: Annotated[str, Field(pattern=_PLAN_SET_DIGEST)]
    approved_document_count: Annotated[int, Field(ge=1)]
    approved_total_attempt_cap: Annotated[int, Field(ge=1)]
    approved_total_request_body_bytes: Annotated[int, Field(ge=1)]
    approved_total_output_token_cap: Annotated[int, Field(ge=256)]
    approved_total_response_body_bytes: Annotated[int, Field(ge=1_024)]
    approved_total_wall_clock_timeout_ms: Annotated[int, Field(ge=1_000)]
    execution_order: Literal["canonical_sequential"]
    items: tuple[DocumentCardCampaignLiveItem, ...]
    document_count: Annotated[int, Field(ge=1)]
    attempt_count: Annotated[int, Field(ge=1)]
    retry_count: Literal[0]
    valid_card_count: Annotated[int, Field(ge=0)]
    failed_count: Annotated[int, Field(ge=0)]
    reported_usage_count: Annotated[int, Field(ge=0)]
    total_latency_ms: Annotated[int, Field(ge=0)]
    total_prompt_tokens: Annotated[int, Field(ge=0)]
    total_completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    observed_total_response_body_bytes: Annotated[int, Field(ge=0)]
    campaign_elapsed_ms: Annotated[int, Field(ge=0)]
    outcome: Literal["all_valid", "completed_with_failures"]
    credential_accessed: Literal[True]
    network_attempted: Literal[True]
    use_scope: Literal["navigation_only_not_evidence"]
    evidence_eligible: Literal[False]
    production_qualified: Literal[False]
    qualification: Literal["live_navigation_campaign_not_document_quality_evidence"]

    @field_validator("items", mode="before")
    @classmethod
    def parse_items(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "DocumentCardCampaignLiveReceipt.items")

    @model_validator(mode="after")
    def validate_order_and_aggregates(self) -> Self:
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("campaign live items are not in canonical order")
        for attribute in ("plan_id", "receipt_id"):
            values = tuple(getattr(item, attribute) for item in self.items)
            if len(values) != len(set(values)):
                raise ValueError(f"campaign live receipt contains duplicate {attribute}")
        valid_count = sum(item.status == "valid_card" for item in self.items)
        usage_items = tuple(item for item in self.items if item.total_tokens is not None)
        expected = (
            len(self.items),
            len(self.items),
            valid_count,
            len(self.items) - valid_count,
            len(usage_items),
            sum(item.latency_ms for item in self.items),
            sum(item.prompt_tokens or 0 for item in self.items),
            sum(item.completion_tokens or 0 for item in self.items),
            sum(item.total_tokens or 0 for item in self.items),
            sum(item.response_body_size_bytes or 0 for item in self.items),
        )
        actual = (
            self.document_count,
            self.attempt_count,
            self.valid_card_count,
            self.failed_count,
            self.reported_usage_count,
            self.total_latency_ms,
            self.total_prompt_tokens,
            self.total_completion_tokens,
            self.total_tokens,
            self.observed_total_response_body_bytes,
        )
        if actual != expected:
            raise ValueError("campaign live aggregates do not rebuild from ordered items")
        if (
            self.approved_document_count != self.document_count
            or self.approved_total_attempt_cap != self.attempt_count
        ):
            raise ValueError("campaign approved counts do not match attempted documents")
        if (
            self.total_completion_tokens > self.approved_total_output_token_cap
            or self.observed_total_response_body_bytes > self.approved_total_response_body_bytes
            or self.campaign_elapsed_ms > self.approved_total_wall_clock_timeout_ms
            or self.total_latency_ms > self.approved_total_wall_clock_timeout_ms
        ):
            raise ValueError("campaign observations exceed approved aggregate caps")
        expected_outcome = (
            "all_valid" if valid_count == len(self.items) else "completed_with_failures"
        )
        if self.outcome != expected_outcome:
            raise ValueError("campaign live outcome does not match ordered items")
        return self


class _DocumentCardCampaignLiveReceiptPayload(_DocumentCardCampaignLiveReceiptFields):
    pass


class DocumentCardCampaignLiveReceipt(_DocumentCardCampaignLiveReceiptFields):
    campaign_receipt_id: Annotated[str, Field(pattern=_CAMPAIGN_RECEIPT_ID)]

    @model_validator(mode="after")
    def validate_campaign_receipt_id(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"campaign_receipt_id"})
        expected = canonical_hash("document-card-campaign-live-receipt", payload)
        if self.campaign_receipt_id != expected:
            raise ValueError("Document Card campaign live receipt ID does not match")
        return self


@dataclass(frozen=True)
class DocumentCardCampaignRunArtifacts:
    receipt: DocumentCardCampaignLiveReceipt
    plan_runs: tuple[DocumentCardRunArtifacts, ...]
    campaign_elapsed_ms: int

    def __post_init__(self) -> None:
        if type(self.campaign_elapsed_ms) is not int or self.campaign_elapsed_ms < 0:
            raise ValueError("campaign elapsed time must be a non-negative integer")


@dataclass(frozen=True)
class _CachedCredentialProvider:
    credential_scope_id: str
    api_key: str = dataclass_field(repr=False)

    def is_configured(self) -> bool:
        return True

    def get_api_key(self) -> str:
        return self.api_key


def _single_bundle(
    campaign: DocumentCardCampaignBundle,
    planned: DocumentCardCampaignPlanBundle,
) -> DocumentCardSmokeBundle:
    return DocumentCardSmokeBundle(
        registry=campaign.registry,
        policy=campaign.policy,
        prompt=campaign.prompt,
        document=planned.document,
        document_map=planned.document_map,
        request=planned.request,
        plan=planned.plan,
    )


def _item_from_run(
    ordinal: int,
    planned: DocumentCardCampaignPlanBundle,
    run: DocumentCardRunArtifacts,
) -> DocumentCardCampaignLiveItem:
    usage = run.receipt.usage
    return DocumentCardCampaignLiveItem(
        ordinal=ordinal,
        plan_id=planned.plan.plan_id,
        wire_body_sha256=planned.plan.wire_body_sha256,
        receipt_id=run.receipt.receipt_id,
        status=run.receipt.status,
        failure_code=run.receipt.failure_code,
        card_id=run.receipt.card_id,
        latency_ms=run.receipt.latency_ms,
        response_body_size_bytes=run.receipt.response_body_size_bytes,
        prompt_tokens=None if usage is None else usage.prompt_tokens,
        completion_tokens=None if usage is None else usage.completion_tokens,
        total_tokens=None if usage is None else usage.total_tokens,
    )


def _seal_campaign_receipt(
    campaign: DocumentCardCampaignBundle,
    plan_runs: tuple[DocumentCardRunArtifacts, ...],
    *,
    campaign_elapsed_ms: int,
) -> DocumentCardCampaignLiveReceipt:
    items = tuple(
        _item_from_run(ordinal, planned, run)
        for ordinal, (planned, run) in enumerate(zip(campaign.plans, plan_runs, strict=True))
    )
    valid_count = sum(item.status == "valid_card" for item in items)
    usage_items = tuple(item for item in items if item.total_tokens is not None)
    inspection = campaign.inspection
    validated = _DocumentCardCampaignLiveReceiptPayload.model_validate(
        {
            "schema_version": DOCUMENT_CARD_CAMPAIGN_LIVE_RECEIPT_SCHEMA_VERSION,
            "campaign_id": inspection.campaign_id,
            "plan_set_digest": inspection.plan_set_digest,
            "approved_document_count": inspection.document_count,
            "approved_total_attempt_cap": inspection.total_attempt_cap,
            "approved_total_request_body_bytes": inspection.total_request_body_bytes,
            "approved_total_output_token_cap": inspection.total_output_token_cap,
            "approved_total_response_body_bytes": inspection.total_response_body_bytes,
            "approved_total_wall_clock_timeout_ms": inspection.total_wall_clock_timeout_ms,
            "execution_order": "canonical_sequential",
            "items": items,
            "document_count": len(items),
            "attempt_count": len(items),
            "retry_count": 0,
            "valid_card_count": valid_count,
            "failed_count": len(items) - valid_count,
            "reported_usage_count": len(usage_items),
            "total_latency_ms": sum(item.latency_ms for item in items),
            "total_prompt_tokens": sum(item.prompt_tokens or 0 for item in items),
            "total_completion_tokens": sum(item.completion_tokens or 0 for item in items),
            "total_tokens": sum(item.total_tokens or 0 for item in items),
            "observed_total_response_body_bytes": sum(
                run.receipt.response_body_size_bytes or 0 for run in plan_runs
            ),
            "campaign_elapsed_ms": campaign_elapsed_ms,
            "outcome": ("all_valid" if valid_count == len(items) else "completed_with_failures"),
            "credential_accessed": True,
            "network_attempted": True,
            "use_scope": "navigation_only_not_evidence",
            "evidence_eligible": False,
            "production_qualified": False,
            "qualification": "live_navigation_campaign_not_document_quality_evidence",
        }
    )
    payload = validated.model_dump(mode="json")
    payload["campaign_receipt_id"] = canonical_hash(
        "document-card-campaign-live-receipt",
        payload,
    )
    return DocumentCardCampaignLiveReceipt.model_validate(payload)


def load_document_card_campaign_live_receipt(
    raw: str | bytes,
) -> DocumentCardCampaignLiveReceipt:
    return load_json_model(
        raw,
        DocumentCardCampaignLiveReceipt,
        "Document Card campaign live receipt",
    )


def verify_document_card_campaign_run(
    campaign: DocumentCardCampaignBundle,
    artifacts: DocumentCardCampaignRunArtifacts,
) -> None:
    verify_document_card_campaign(campaign)
    if not isinstance(artifacts, DocumentCardCampaignRunArtifacts):
        raise TypeError("Document Card campaign run uses an unsupported type")
    if len(artifacts.plan_runs) != len(campaign.plans):
        raise ValueError("Document Card campaign run count differs from exact plans")
    canonical_receipt = DocumentCardCampaignLiveReceipt.model_validate(
        artifacts.receipt.model_dump(mode="json")
    )
    for planned, run in zip(campaign.plans, artifacts.plan_runs, strict=True):
        _verify_run_artifacts(_single_bundle(campaign, planned), run)
    expected = _seal_campaign_receipt(
        campaign,
        artifacts.plan_runs,
        campaign_elapsed_ms=artifacts.campaign_elapsed_ms,
    )
    if canonical_receipt != expected:
        raise ValueError("Document Card campaign live receipt differs from run artifacts")


def _verify_exact_approval(
    campaign: DocumentCardCampaignBundle,
    *,
    approved_campaign_id: str,
    approved_plan_set_digest: str,
    approved_document_count: int,
    approved_total_attempt_cap: int,
    approved_total_request_body_bytes: int,
    approved_total_output_token_cap: int,
    approved_total_response_body_bytes: int,
    approved_total_wall_clock_timeout_ms: int,
    acknowledgement: str,
) -> None:
    inspection = campaign.inspection
    comparisons = (
        (approved_campaign_id, inspection.campaign_id, "approved_campaign_id_mismatch"),
        (
            approved_plan_set_digest,
            inspection.plan_set_digest,
            "approved_plan_set_digest_mismatch",
        ),
        (
            approved_document_count,
            inspection.document_count,
            "approved_document_count_mismatch",
        ),
        (
            approved_total_attempt_cap,
            inspection.total_attempt_cap,
            "approved_total_attempt_cap_mismatch",
        ),
        (
            approved_total_request_body_bytes,
            inspection.total_request_body_bytes,
            "approved_total_request_body_bytes_mismatch",
        ),
        (
            approved_total_output_token_cap,
            inspection.total_output_token_cap,
            "approved_total_output_token_cap_mismatch",
        ),
        (
            approved_total_response_body_bytes,
            inspection.total_response_body_bytes,
            "approved_total_response_body_bytes_mismatch",
        ),
        (
            approved_total_wall_clock_timeout_ms,
            inspection.total_wall_clock_timeout_ms,
            "approved_total_wall_clock_timeout_ms_mismatch",
        ),
    )
    for approved, expected, code in comparisons:
        if type(approved) is not type(expected) or approved != expected:
            raise DocumentCardCampaignLiveError(code)
    if acknowledgement != DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT:
        raise DocumentCardCampaignLiveError("campaign_export_acknowledgement_missing")


def _marker_path(state_dir: Path, plan_id: str) -> Path:
    return state_dir / f"{plan_id.rsplit(':', 1)[-1]}.consumed.json"


def _preflight_replay_state(
    state_dir: Path,
    campaign: DocumentCardCampaignBundle,
) -> None:
    if not state_dir.name or state_dir.name in {".", ".."}:
        raise DocumentCardCampaignLiveError("unsafe_state_directory")
    try:
        metadata = os.lstat(state_dir)
    except FileNotFoundError:
        try:
            parent_metadata = os.lstat(state_dir.parent)
        except OSError:
            raise DocumentCardCampaignLiveError("unsafe_state_parent") from None
        if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(parent_metadata.st_mode):
            raise DocumentCardCampaignLiveError("unsafe_state_parent") from None
        return
    except OSError:
        raise DocumentCardCampaignLiveError("unsafe_state_directory") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise DocumentCardCampaignLiveError("unsafe_state_directory")
    for planned in campaign.plans:
        try:
            os.lstat(_marker_path(state_dir, planned.plan.plan_id))
        except FileNotFoundError:
            continue
        except OSError:
            raise DocumentCardCampaignLiveError("attempt_replay_preflight_failed") from None
        raise DocumentCardCampaignLiveError("attempt_already_consumed")


def run_document_card_campaign_live_once(
    *,
    campaign: DocumentCardCampaignBundle,
    approved_campaign_id: str,
    approved_plan_set_digest: str,
    approved_document_count: int,
    approved_total_attempt_cap: int,
    approved_total_request_body_bytes: int,
    approved_total_output_token_cap: int,
    approved_total_response_body_bytes: int,
    approved_total_wall_clock_timeout_ms: int,
    acknowledgement: str,
    state_dir: Path,
    output_root: Path,
    credential_provider: DeepSeekCredentialProvider,
    transport: DocumentCardTransport | None = None,
) -> DocumentCardCampaignRunArtifacts:
    verify_document_card_campaign(campaign)
    _verify_exact_approval(
        campaign,
        approved_campaign_id=approved_campaign_id,
        approved_plan_set_digest=approved_plan_set_digest,
        approved_document_count=approved_document_count,
        approved_total_attempt_cap=approved_total_attempt_cap,
        approved_total_request_body_bytes=approved_total_request_body_bytes,
        approved_total_output_token_cap=approved_total_output_token_cap,
        approved_total_response_body_bytes=approved_total_response_body_bytes,
        approved_total_wall_clock_timeout_ms=approved_total_wall_clock_timeout_ms,
        acknowledgement=acknowledgement,
    )
    _preflight_replay_state(state_dir, campaign)
    layout = _campaign_artifact_layout(campaign, output_root=output_root)
    _preflight_new_live_artifacts(layout)

    if not credential_provider.is_configured():
        raise DocumentCardCampaignLiveError("credential_not_configured")
    try:
        api_key = credential_provider.get_api_key()
    except DeepSeekCredentialUnavailableError:
        raise DocumentCardCampaignLiveError("credential_unavailable") from None
    if (
        not api_key
        or api_key != api_key.strip()
        or len(api_key) > 4_096
        or any(ord(character) < 33 or ord(character) == 127 for character in api_key)
    ):
        raise DocumentCardCampaignLiveError("credential_unavailable")
    cached_provider = _CachedCredentialProvider(
        credential_scope_id=credential_provider.credential_scope_id,
        api_key=api_key,
    )

    plan_runs: list[DocumentCardRunArtifacts] = []
    started_ns = time.monotonic_ns()
    for ordinal, planned in enumerate(campaign.plans):
        elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
        remaining_ms = campaign.inspection.total_wall_clock_timeout_ms - elapsed_ms
        if remaining_ms < planned.plan.wall_clock_timeout_ms:
            raise DocumentCardCampaignLiveError("campaign_wall_clock_budget_insufficient")
        try:
            run = run_document_card_live_once(
                bundle=_single_bundle(campaign, planned),
                approved_plan_id=planned.plan.plan_id,
                approved_wire_body_sha256=planned.plan.wire_body_sha256,
                reserved_max_output_tokens=planned.plan.wire_payload.max_tokens,
                acknowledgement=DOCUMENT_CARD_SMOKE_ACKNOWLEDGEMENT,
                state_dir=state_dir,
                credential_provider=cached_provider,
                transport=transport,
            )
        except DocumentCardSmokeError as exc:
            raise DocumentCardCampaignLiveError(f"plan_control_failed:{exc.code}") from None
        plan_runs.append(run)
        _write_plan_run(layout.plan_directories[ordinal], run)

    campaign_elapsed_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    if campaign_elapsed_ms > campaign.inspection.total_wall_clock_timeout_ms:
        raise DocumentCardCampaignLiveError("campaign_wall_clock_budget_exceeded")
    artifacts = DocumentCardCampaignRunArtifacts(
        receipt=_seal_campaign_receipt(
            campaign,
            tuple(plan_runs),
            campaign_elapsed_ms=campaign_elapsed_ms,
        ),
        plan_runs=tuple(plan_runs),
        campaign_elapsed_ms=campaign_elapsed_ms,
    )
    verify_document_card_campaign_run(campaign, artifacts)
    _write_campaign_receipt(layout, artifacts.receipt)
    return artifacts


def _json_bytes(value: object) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, FrozenModel) else value
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _require_real_directory(path: Path, code: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise DocumentCardCampaignLiveError(code) from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DocumentCardCampaignLiveError(code)


def _preflight_artifact(path: Path, content: bytes) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise DocumentCardCampaignLiveError("artifact_collision") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or path.read_bytes() != content
    ):
        raise DocumentCardCampaignLiveError("artifact_collision")


def _require_exact_offline_artifact(path: Path, content: bytes) -> None:
    try:
        metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or path.read_bytes() != content
        ):
            raise DocumentCardCampaignLiveError("campaign_offline_artifact_mismatch")
    except OSError:
        raise DocumentCardCampaignLiveError("campaign_offline_artifact_mismatch") from None


def _require_artifact_absent(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise DocumentCardCampaignLiveError("artifact_collision") from None
    raise DocumentCardCampaignLiveError("artifact_collision")


@dataclass(frozen=True)
class _CampaignArtifactLayout:
    campaign_directory: Path
    plan_directories: tuple[Path, ...]


def _campaign_artifact_layout(
    campaign: DocumentCardCampaignBundle,
    *,
    output_root: Path,
) -> _CampaignArtifactLayout:
    campaign_digest = campaign.inspection.campaign_id.rsplit(":", 1)[-1]
    campaign_directory = output_root / campaign_digest
    plans_directory = campaign_directory / "plans"
    _require_real_directory(output_root, "campaign_output_root_missing")
    _require_real_directory(campaign_directory, "campaign_artifact_directory_missing")
    _require_real_directory(plans_directory, "campaign_plans_directory_missing")
    _require_exact_offline_artifact(
        campaign_directory / "00_campaign-selection.json",
        _json_bytes(campaign.selection),
    )
    _require_exact_offline_artifact(
        campaign_directory / "01_campaign-inspection.json",
        _json_bytes(campaign.inspection),
    )

    plan_directories: list[Path] = []
    for inspected, planned in zip(
        campaign.inspection.plans,
        campaign.plans,
        strict=True,
    ):
        plan_digest = planned.plan.plan_id.rsplit(":", 1)[-1]
        plan_directory = plans_directory / f"{inspected.ordinal:02d}_{plan_digest}"
        _require_real_directory(plan_directory, "campaign_plan_directory_missing")
        source_manifest = {
            "schema_version": "document-card-source-manifest-v1",
            "document_id": planned.document.document_id,
            "source_ref": planned.document.source_ref.model_dump(mode="json"),
            "normalized_body_hash": planned.document_map.normalized_body_hash,
            "document_map_id": planned.document_map.map_id,
            "request_id": planned.request.request_id,
            "plan_id": planned.plan.plan_id,
            "prompt_version": campaign.prompt.prompt_version,
            "prompt_hash": campaign.prompt.prompt_hash,
            "export_policy_fingerprint": campaign.policy.fingerprint,
            "campaign_id": campaign.inspection.campaign_id,
            "plan_set_digest": campaign.inspection.plan_set_digest,
            "qualification": "pinned_source_identity_not_execution_or_publication_approval",
        }
        plan_inspection = {
            "schema_version": "document-card-campaign-plan-inspection-v1",
            "mode": "inspect_only",
            "network_attempted": False,
            "credential_accessed": False,
            "execution_authorized": False,
            "campaign_id": campaign.inspection.campaign_id,
            "plan_set_digest": campaign.inspection.plan_set_digest,
            **inspected.model_dump(mode="json"),
            "qualification": "offline_plan_not_execution_or_document_quality_evidence",
        }
        expected_files = {
            "00_source-manifest.json": _json_bytes(source_manifest),
            "01_source.md": planned.document.body.encode("utf-8"),
            "02_document-map.json": _json_bytes(planned.document_map),
            "03_request.json": _json_bytes(planned.request),
            "04_dispatch-plan.json": _json_bytes(planned.plan),
            "05_inspection.json": _json_bytes(plan_inspection),
        }
        for name, content in expected_files.items():
            _require_exact_offline_artifact(plan_directory / name, content)
        plan_directories.append(plan_directory)
    return _CampaignArtifactLayout(
        campaign_directory=campaign_directory,
        plan_directories=tuple(plan_directories),
    )


def verify_document_card_campaign_materialization(
    campaign: DocumentCardCampaignBundle,
    *,
    output_root: Path,
) -> Path:
    verify_document_card_campaign(campaign)
    return _campaign_artifact_layout(campaign, output_root=output_root).campaign_directory


def _plan_artifacts(
    plan_directory: Path,
    run: DocumentCardRunArtifacts,
) -> tuple[tuple[tuple[Path, bytes], ...], tuple[Path, ...]]:
    optional = {
        "06_provider-response.raw.json": run.raw_response_body,
        "07_document-card-draft.json": None if run.draft is None else _json_bytes(run.draft),
        "08_document-card.json": None if run.card is None else _json_bytes(run.card),
    }
    writes = tuple(
        (plan_directory / name, content)
        for name, content in optional.items()
        if content is not None
    ) + ((plan_directory / "09_receipt.json", _json_bytes(run.receipt)),)
    forbidden = tuple(
        plan_directory / name for name, content in optional.items() if content is None
    )
    return writes, forbidden


def _preflight_new_live_artifacts(layout: _CampaignArtifactLayout) -> None:
    _require_artifact_absent(layout.campaign_directory / "02_campaign-live-receipt.json")
    for plan_directory in layout.plan_directories:
        for name in (
            "06_provider-response.raw.json",
            "07_document-card-draft.json",
            "08_document-card.json",
            "09_receipt.json",
        ):
            _require_artifact_absent(plan_directory / name)


def _write_plan_run(plan_directory: Path, run: DocumentCardRunArtifacts) -> None:
    writes, forbidden = _plan_artifacts(plan_directory, run)
    for path, content in writes:
        _preflight_artifact(path, content)
    for path in forbidden:
        _require_artifact_absent(path)
    try:
        for path, content in writes:
            _write_or_verify(path, content)
    except (DocumentCardSmokeError, OSError, ValueError):
        raise DocumentCardCampaignLiveError("artifact_collision") from None


def _write_campaign_receipt(
    layout: _CampaignArtifactLayout,
    receipt: DocumentCardCampaignLiveReceipt,
) -> None:
    path = layout.campaign_directory / "02_campaign-live-receipt.json"
    content = _json_bytes(receipt)
    _preflight_artifact(path, content)
    try:
        _write_or_verify(path, content)
    except (DocumentCardSmokeError, OSError, ValueError):
        raise DocumentCardCampaignLiveError("artifact_collision") from None


def materialize_document_card_campaign_run(
    campaign: DocumentCardCampaignBundle,
    artifacts: DocumentCardCampaignRunArtifacts,
    *,
    output_root: Path,
) -> Path:
    verify_document_card_campaign_run(campaign, artifacts)
    layout = _campaign_artifact_layout(campaign, output_root=output_root)
    for plan_directory, run in zip(
        layout.plan_directories,
        artifacts.plan_runs,
        strict=True,
    ):
        _write_plan_run(plan_directory, run)
    _write_campaign_receipt(layout, artifacts.receipt)
    return layout.campaign_directory


__all__ = [
    "DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT",
    "DOCUMENT_CARD_CAMPAIGN_LIVE_RECEIPT_SCHEMA_VERSION",
    "DocumentCardCampaignLiveError",
    "DocumentCardCampaignLiveItem",
    "DocumentCardCampaignLiveReceipt",
    "DocumentCardCampaignRunArtifacts",
    "load_document_card_campaign_live_receipt",
    "materialize_document_card_campaign_run",
    "run_document_card_campaign_live_once",
    "verify_document_card_campaign_materialization",
    "verify_document_card_campaign_run",
]
