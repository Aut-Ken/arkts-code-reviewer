from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

import tools.run_deepseek_document_card_campaign as campaign_cli
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
)
from arkts_code_reviewer.knowledge.document_first.campaign import (
    DocumentCardCampaignBundle,
)
from arkts_code_reviewer.knowledge.document_first.campaign_live import (
    DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
    DocumentCardCampaignRunArtifacts,
)

CAMPAIGN_ID = "document-card-campaign:sha256:" + "1" * 64
PLAN_SET_DIGEST = "document-card-campaign-plan-set:sha256:" + "2" * 64
CAMPAIGN_RECEIPT_ID = "document-card-campaign-live-receipt:sha256:" + "3" * 64
PLAN_ID = "document-card-plan:sha256:" + "4" * 64
RECEIPT_ID = "document-card-live-receipt:sha256:" + "5" * 64
CARD_ID = "document-card:sha256:" + "6" * 64
SECRET = "CLI_SECRET_MUST_NOT_APPEAR"


@dataclass(frozen=True)
class _Inspection:
    campaign_id: str = CAMPAIGN_ID
    plan_set_digest: str = PLAN_SET_DIGEST
    document_count: int = 1
    total_attempt_cap: int = 1
    total_request_body_bytes: int = 12_345
    total_output_token_cap: int = 4_096
    total_response_body_bytes: int = 2_000_000
    total_wall_clock_timeout_ms: int = 120_000


@dataclass(frozen=True)
class _Bundle:
    inspection: _Inspection = _Inspection()


@dataclass(frozen=True)
class _Item:
    ordinal: int = 0
    plan_id: str = PLAN_ID
    receipt_id: str = RECEIPT_ID
    status: str = "valid_card"
    failure_code: None = None
    card_id: str = CARD_ID


@dataclass(frozen=True)
class _Receipt:
    campaign_id: str = CAMPAIGN_ID
    plan_set_digest: str = PLAN_SET_DIGEST
    campaign_receipt_id: str = CAMPAIGN_RECEIPT_ID
    document_count: int = 1
    attempt_count: int = 1
    retry_count: int = 0
    valid_card_count: int = 1
    failed_count: int = 0
    reported_usage_count: int = 1
    total_latency_ms: int = 25
    total_prompt_tokens: int = 100
    total_completion_tokens: int = 20
    total_tokens: int = 120
    observed_total_response_body_bytes: int = 2_048
    campaign_elapsed_ms: int = 30
    outcome: str = "all_valid"
    items: tuple[_Item, ...] = (_Item(),)


@dataclass(frozen=True)
class _Artifacts:
    receipt: _Receipt = _Receipt()


class _Credential:
    credential_scope_id = "deepseek-credential-scope:sha256:" + "7" * 64

    def is_configured(self) -> bool:
        return True

    def get_api_key(self) -> str:
        return SECRET


def _bundle() -> DocumentCardCampaignBundle:
    return cast(DocumentCardCampaignBundle, _Bundle())


def _artifacts() -> DocumentCardCampaignRunArtifacts:
    return cast(DocumentCardCampaignRunArtifacts, _Artifacts())


def _patch_offline(
    monkeypatch: pytest.MonkeyPatch,
    bundle: DocumentCardCampaignBundle,
) -> list[Path]:
    verified: list[Path] = []

    def fake_prepare(**kwargs: object) -> DocumentCardCampaignBundle:
        assert kwargs
        return bundle

    def fake_verify(
        received: DocumentCardCampaignBundle,
        *,
        output_root: Path,
    ) -> None:
        assert received is bundle
        verified.append(output_root)

    monkeypatch.setattr(campaign_cli, "prepare_document_card_campaign", fake_prepare)
    monkeypatch.setattr(
        campaign_cli,
        "verify_document_card_campaign_materialization",
        fake_verify,
    )
    return verified


def _live_args(output_root: Path, state_dir: Path) -> list[str]:
    inspection = _Inspection()
    return [
        "--execute-live",
        "--approve-campaign-id",
        inspection.campaign_id,
        "--approve-plan-set-digest",
        inspection.plan_set_digest,
        "--approve-document-count",
        str(inspection.document_count),
        "--approve-total-attempt-cap",
        str(inspection.total_attempt_cap),
        "--approve-total-request-body-bytes",
        str(inspection.total_request_body_bytes),
        "--approve-total-output-token-cap",
        str(inspection.total_output_token_cap),
        "--approve-total-response-body-bytes",
        str(inspection.total_response_body_bytes),
        "--approve-total-wall-clock-timeout-ms",
        str(inspection.total_wall_clock_timeout_ms),
        "--acknowledge-document-export",
        DOCUMENT_CARD_CAMPAIGN_LIVE_ACKNOWLEDGEMENT,
        "--state-dir",
        str(state_dir),
        "--output-root",
        str(output_root),
    ]


def test_default_is_offline_safe_inspection_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _bundle()
    verified = _patch_offline(monkeypatch, bundle)
    factory_calls = 0

    def forbidden_factory() -> DeepSeekCredentialProvider:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("credential provider must not be built")

    assert (
        campaign_cli.main(
            ["--output-root", str(tmp_path / "offline")],
            credential_provider_factory=forbidden_factory,
        )
        == 0
    )
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert output.err == ""
    assert summary["mode"] == "inspect_only"
    assert summary["network_attempted"] is False
    assert summary["credential_accessed"] is False
    assert summary["campaign_id"] == CAMPAIGN_ID
    assert summary["document_count"] == 1
    assert "items" not in summary
    assert factory_calls == 0
    assert verified == [tmp_path / "offline"]


def test_transport_gate_and_exact_mismatch_precede_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_offline(monkeypatch, _bundle())
    factory_calls = 0

    def forbidden_factory() -> DeepSeekCredentialProvider:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("credential provider must not be built")

    args = _live_args(tmp_path / "offline", tmp_path / "state")
    assert campaign_cli.main(args, credential_provider_factory=forbidden_factory) == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["error_code"] == "real_transport_not_explicitly_allowed"
    assert factory_calls == 0

    mismatch = args.copy()
    mismatch[mismatch.index("--approve-document-count") + 1] = "2"
    assert (
        campaign_cli.main(
            mismatch,
            credential_provider_factory=forbidden_factory,
            test_transport=cast(object, object()),
        )
        == 2
    )
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["error_code"] == "approved_document_count_mismatch"
    assert factory_calls == 0


def test_explicit_real_mode_passes_all_controls_and_prints_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _bundle()
    _patch_offline(monkeypatch, bundle)
    captured_kwargs: dict[str, object] = {}

    def fake_run(**kwargs: object) -> DocumentCardCampaignRunArtifacts:
        captured_kwargs.update(kwargs)
        return _artifacts()

    monkeypatch.setattr(campaign_cli, "run_document_card_campaign_live_once", fake_run)
    output_root = tmp_path / "offline-artifacts"
    state_dir = tmp_path / "private-state"
    args = [*_live_args(output_root, state_dir), "--allow-real-transport"]

    assert (
        campaign_cli.main(
            args,
            credential_provider_factory=lambda: _Credential(),
        )
        == 0
    )
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert output.err == ""
    assert summary["mode"] == "live_campaign"
    assert summary["network_attempted"] is True
    assert summary["campaign_receipt_id"] == CAMPAIGN_RECEIPT_ID
    assert summary["outcome"] == "all_valid"
    assert summary["observed_total_response_body_bytes"] == 2_048
    assert summary["campaign_elapsed_ms"] == 30
    assert summary["items"] == [
        {
            "card_id": CARD_ID,
            "failure_code": None,
            "ordinal": 0,
            "plan_id": PLAN_ID,
            "receipt_id": RECEIPT_ID,
            "status": "valid_card",
        }
    ]
    assert captured_kwargs["campaign"] is bundle
    assert captured_kwargs["approved_document_count"] == 1
    assert captured_kwargs["output_root"] == output_root
    assert captured_kwargs["state_dir"] == state_dir
    assert captured_kwargs["transport"] is None
    rendered = output.out + output.err
    assert SECRET not in rendered
    assert str(output_root) not in rendered
    assert str(state_dir) not in rendered


def test_invalid_argument_does_not_echo_value_or_touch_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_argument = "ARBITRARY_SECRET_ARGUMENT_MUST_NOT_ECHO"
    factory_calls = 0

    def forbidden_factory() -> DeepSeekCredentialProvider:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("credential provider must not be built")

    assert (
        campaign_cli.main(
            ["--unknown-sensitive-option", secret_argument],
            credential_provider_factory=forbidden_factory,
        )
        == 2
    )
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["error_code"] == "invalid_arguments"
    assert secret_argument not in output.out + output.err
    assert factory_calls == 0
