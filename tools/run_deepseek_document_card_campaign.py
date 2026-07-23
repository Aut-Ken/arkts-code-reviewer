#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    EnvironmentDeepSeekCredentialProvider,
)
from arkts_code_reviewer.knowledge.document_first._canonical import canonical_json
from arkts_code_reviewer.knowledge.document_first.campaign import (
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT,
    DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    DocumentCardCampaignBundle,
    prepare_document_card_campaign,
)
from arkts_code_reviewer.knowledge.document_first.campaign_live import (
    DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
    DocumentCardCampaignLiveError,
    DocumentCardCampaignRunArtifacts,
    run_document_card_campaign_live_once,
    verify_document_card_campaign_materialization,
)
from arkts_code_reviewer.knowledge.document_first.live_smoke import (
    DocumentCardTransport,
)
from arkts_code_reviewer.knowledge.registry import DEFAULT_SOURCE_REGISTRY
from arkts_code_reviewer.knowledge.seed import DEFAULT_KNOWLEDGE_SEED

_SUMMARY_SCHEMA_VERSION = "document-card-campaign-cli-summary-v1"


class _CliPreflightError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _CliPreflightError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description=(
            "Inspect or explicitly run the pinned DeepSeek Document Card campaign. "
            "Inspection is offline; live execution is fail-closed."
        )
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--allow-real-transport", action="store_true")
    parser.add_argument("--approve-campaign-id")
    parser.add_argument("--approve-plan-set-digest")
    parser.add_argument("--approve-document-count", type=int)
    parser.add_argument("--approve-total-attempt-cap", type=int)
    parser.add_argument("--approve-total-request-body-bytes", type=int)
    parser.add_argument("--approve-total-output-token-cap", type=int)
    parser.add_argument("--approve-total-response-body-bytes", type=int)
    parser.add_argument("--approve-total-wall-clock-timeout-ms", type=int)
    parser.add_argument("--acknowledge-document-export")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--seed", type=Path, default=DEFAULT_KNOWLEDGE_SEED)
    parser.add_argument(
        "--selection",
        type=Path,
        default=DEFAULT_DOCUMENT_CARD_CAMPAIGN_SELECTION_PATH,
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_DOCUMENT_CARD_CAMPAIGN_EXPORT_POLICY_PATH,
    )
    parser.add_argument("--prompt", type=Path)
    return parser


def _safe_error(*, code: str, attempted: bool | None) -> dict[str, object]:
    return {
        "schema_version": _SUMMARY_SCHEMA_VERSION,
        "mode": "campaign_preflight" if attempted is False else "campaign_attempt",
        "network_attempted": attempted,
        "error_code": code,
        "evidence_eligible": False,
        "production_qualified": False,
        "qualification": "campaign_cli_error_not_document_quality_evidence",
    }


def _inspection_summary(bundle: DocumentCardCampaignBundle) -> dict[str, object]:
    inspection = bundle.inspection
    return {
        "schema_version": _SUMMARY_SCHEMA_VERSION,
        "mode": "inspect_only",
        "network_attempted": False,
        "credential_accessed": False,
        "execution_status": "not_authorized",
        "campaign_id": inspection.campaign_id,
        "plan_set_digest": inspection.plan_set_digest,
        "document_count": inspection.document_count,
        "total_attempt_cap": inspection.total_attempt_cap,
        "total_request_body_bytes": inspection.total_request_body_bytes,
        "total_output_token_cap": inspection.total_output_token_cap,
        "total_response_body_bytes": inspection.total_response_body_bytes,
        "total_wall_clock_timeout_ms": inspection.total_wall_clock_timeout_ms,
        "evidence_eligible": False,
        "production_qualified": False,
        "qualification": "offline_campaign_inspection_not_document_quality_evidence",
    }


def _live_summary(
    artifacts: DocumentCardCampaignRunArtifacts,
    *,
    real_transport: bool,
) -> dict[str, object]:
    receipt = artifacts.receipt
    return {
        "schema_version": _SUMMARY_SCHEMA_VERSION,
        "mode": "live_campaign",
        "network_attempted": True if real_transport else None,
        "execution_status": "completed",
        "campaign_id": receipt.campaign_id,
        "plan_set_digest": receipt.plan_set_digest,
        "campaign_receipt_id": receipt.campaign_receipt_id,
        "document_count": receipt.document_count,
        "attempt_count": receipt.attempt_count,
        "retry_count": receipt.retry_count,
        "valid_card_count": receipt.valid_card_count,
        "failed_count": receipt.failed_count,
        "reported_usage_count": receipt.reported_usage_count,
        "total_latency_ms": receipt.total_latency_ms,
        "total_prompt_tokens": receipt.total_prompt_tokens,
        "total_completion_tokens": receipt.total_completion_tokens,
        "total_tokens": receipt.total_tokens,
        "observed_total_response_body_bytes": receipt.observed_total_response_body_bytes,
        "campaign_elapsed_ms": receipt.campaign_elapsed_ms,
        "outcome": receipt.outcome,
        "items": [
            {
                "ordinal": item.ordinal,
                "plan_id": item.plan_id,
                "receipt_id": item.receipt_id,
                "status": item.status,
                "failure_code": item.failure_code,
                "card_id": item.card_id,
            }
            for item in receipt.items
        ],
        "evidence_eligible": False,
        "production_qualified": False,
        "qualification": "live_navigation_campaign_not_document_quality_evidence",
    }


def _verify_exact_controls(args: argparse.Namespace, bundle: DocumentCardCampaignBundle) -> None:
    inspection = bundle.inspection
    comparisons = (
        (args.approve_campaign_id, inspection.campaign_id, "approved_campaign_id_mismatch"),
        (
            args.approve_plan_set_digest,
            inspection.plan_set_digest,
            "approved_plan_set_digest_mismatch",
        ),
        (
            args.approve_document_count,
            inspection.document_count,
            "approved_document_count_mismatch",
        ),
        (
            args.approve_total_attempt_cap,
            inspection.total_attempt_cap,
            "approved_total_attempt_cap_mismatch",
        ),
        (
            args.approve_total_request_body_bytes,
            inspection.total_request_body_bytes,
            "approved_total_request_body_bytes_mismatch",
        ),
        (
            args.approve_total_output_token_cap,
            inspection.total_output_token_cap,
            "approved_total_output_token_cap_mismatch",
        ),
        (
            args.approve_total_response_body_bytes,
            inspection.total_response_body_bytes,
            "approved_total_response_body_bytes_mismatch",
        ),
        (
            args.approve_total_wall_clock_timeout_ms,
            inspection.total_wall_clock_timeout_ms,
            "approved_total_wall_clock_timeout_ms_mismatch",
        ),
    )
    for approved, expected, code in comparisons:
        if type(approved) is not type(expected) or approved != expected:
            raise _CliPreflightError(code)
    if args.acknowledge_document_export != DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT:
        raise _CliPreflightError("campaign_export_acknowledgement_missing")


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_provider_factory: Callable[[], DeepSeekCredentialProvider] = (
        EnvironmentDeepSeekCredentialProvider
    ),
    test_transport: DocumentCardTransport | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
        bundle = prepare_document_card_campaign(
            registry_path=args.registry,
            seed_path=args.seed,
            selection_path=args.selection,
            policy_path=args.policy,
            prompt_path=args.prompt,
        )
        effective_output_root = (
            DEFAULT_DOCUMENT_CARD_CAMPAIGN_OUTPUT_ROOT
            if args.output_root is None
            else args.output_root
        )
        verify_document_card_campaign_materialization(
            bundle,
            output_root=effective_output_root,
        )
    except (
        _CliPreflightError,
        DocumentCardCampaignLiveError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, _CliPreflightError | DocumentCardCampaignLiveError):
            code = exc.code
        else:
            code = "inspection_invalid"
        print(canonical_json(_safe_error(code=code, attempted=False)))
        return 2

    if not args.execute_live:
        print(canonical_json(_inspection_summary(bundle)))
        return 0
    if test_transport is None and not args.allow_real_transport:
        print(
            canonical_json(
                _safe_error(code="real_transport_not_explicitly_allowed", attempted=False)
            )
        )
        return 2
    if test_transport is not None and args.allow_real_transport:
        print(canonical_json(_safe_error(code="transport_mode_conflict", attempted=False)))
        return 2

    required = (
        args.approve_campaign_id,
        args.approve_plan_set_digest,
        args.approve_document_count,
        args.approve_total_attempt_cap,
        args.approve_total_request_body_bytes,
        args.approve_total_output_token_cap,
        args.approve_total_response_body_bytes,
        args.approve_total_wall_clock_timeout_ms,
        args.acknowledge_document_export,
        args.state_dir,
        args.output_root,
    )
    if any(value is None for value in required):
        print(canonical_json(_safe_error(code="live_controls_incomplete", attempted=False)))
        return 2
    try:
        _verify_exact_controls(args, bundle)
    except _CliPreflightError as exc:
        print(canonical_json(_safe_error(code=exc.code, attempted=False)))
        return 2

    try:
        credential_provider = credential_provider_factory()
    except Exception:
        print(
            canonical_json(
                _safe_error(code="credential_provider_unavailable", attempted=False)
            )
        )
        return 2
    try:
        artifacts = run_document_card_campaign_live_once(
            campaign=bundle,
            approved_campaign_id=args.approve_campaign_id,
            approved_plan_set_digest=args.approve_plan_set_digest,
            approved_document_count=args.approve_document_count,
            approved_total_attempt_cap=args.approve_total_attempt_cap,
            approved_total_request_body_bytes=args.approve_total_request_body_bytes,
            approved_total_output_token_cap=args.approve_total_output_token_cap,
            approved_total_response_body_bytes=args.approve_total_response_body_bytes,
            approved_total_wall_clock_timeout_ms=args.approve_total_wall_clock_timeout_ms,
            acknowledgement=args.acknowledge_document_export,
            state_dir=args.state_dir,
            output_root=args.output_root,
            credential_provider=credential_provider,
            transport=test_transport,
        )
    except DocumentCardCampaignLiveError as exc:
        print(canonical_json(_safe_error(code=exc.code, attempted=None)))
        return 3
    except (OSError, TypeError, ValueError):
        print(
            canonical_json(
                _safe_error(code="campaign_runtime_or_integrity_invalid", attempted=None)
            )
        )
        return 3
    except Exception:
        print(canonical_json(_safe_error(code="campaign_runtime_error", attempted=None)))
        return 3

    print(canonical_json(_live_summary(artifacts, real_transport=test_transport is None)))
    return 0 if artifacts.receipt.outcome == "all_valid" else 3


if __name__ == "__main__":
    raise SystemExit(main())
