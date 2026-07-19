from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import arkts_code_reviewer.hybrid_analysis.campaign_live_smoke as campaign_live_smoke
from arkts_code_reviewer.hybrid_analysis.campaign_live_smoke import (
    REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
    CampaignSmokePreflightError,
    build_campaign_inspection_summary,
    build_local_campaign_egress_approval,
    build_local_campaign_plan_reservations,
    build_repository_synthetic_campaign_bundle,
    main,
    run_repository_synthetic_campaign,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekHttpResponse,
)
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchPlan,
)

_API_KEY_SECRET = "API_KEY_SECRET_MUST_NOT_APPEAR"
_RAW_SECRET = "RAW_RESPONSE_SECRET_MUST_NOT_APPEAR"
_MODEL_REASON_SECRET = "MODEL_REASON_SECRET_MUST_NOT_APPEAR"


@dataclass
class _Credential:
    configured: bool = True
    factory_calls: int = 0
    configured_checks: int = 0
    key_reads: int = 0

    @property
    def credential_scope_id(self) -> str:
        return "deepseek-credential-scope:sha256:" + "d" * 64

    def is_configured(self) -> bool:
        self.configured_checks += 1
        return self.configured

    def get_api_key(self) -> str:
        self.key_reads += 1
        return _API_KEY_SECRET


@dataclass
class _CampaignTransport:
    response_by_plan_id: dict[str, DeepSeekHttpResponse]
    calls: list[str] = field(default_factory=list)
    seen_keys: list[str] = field(default_factory=list)

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.max_attempts == 1
        self.calls.append(plan.plan_id)
        self.seen_keys.append(api_key)
        return self.response_by_plan_id[plan.plan_id]


def _provider_body(unit, *, invalid_inner: bool = False) -> bytes:  # type: ignore[no-untyped-def]
    visible_line = unit.model_view.code.line_numbers[0]
    judgments: list[dict[str, object]] = []
    for contract in unit.request.tag_contract_views:
        if contract.tag_id == "has_logging":
            judgments.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "positive",
                    "evidence_lines": [visible_line],
                    "reason_code": "direct_unit_semantic_evidence",
                    "reason": _MODEL_REASON_SECRET,
                }
            )
        else:
            judgments.append(
                {
                    "tag_id": contract.tag_id,
                    "decision": "not_supported",
                    "evidence_lines": [],
                    "reason_code": "no_support_in_complete_view",
                    "reason": None,
                }
            )
    if invalid_inner:
        judgments.pop()
    content = json.dumps(
        {"judgments": judgments},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return json.dumps(
        {
            "id": "chatcmpl-fixed-campaign",
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {
                        "content": content,
                        "role": "assistant",
                        "reasoning_content": None,
                    },
                    "logprobs": None,
                }
            ],
            "created": 1_750_000_000,
            "model": "deepseek-v4-pro",
            "object": "chat.completion",
            "system_fingerprint": "fp-fixed-campaign-test",
            "usage": {
                "completion_tokens": 50,
                "prompt_tokens": 100,
                "total_tokens": 150,
                "prompt_cache_hit_tokens": 25,
                "prompt_cache_miss_tokens": 75,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _transport(*, invalid_ordinal: int | None = None) -> _CampaignTransport:
    bundle = build_repository_synthetic_campaign_bundle()
    responses = {
        unit.plan.plan_id: DeepSeekHttpResponse(
            status_code=200,
            body=_provider_body(unit, invalid_inner=ordinal == invalid_ordinal),
            retry_after_ms=None,
            latency_ms=5,
        )
        for ordinal, unit in enumerate(bundle.campaign.units, start=1)
    }
    return _CampaignTransport(responses)


def _execute_args(bundle, state_dir: Path) -> list[str]:  # type: ignore[no-untyped-def]
    caps = bundle.caps
    return [
        "--execute-live",
        "--approve-campaign-id",
        bundle.case.campaign_id,
        "--approve-plan-set-digest",
        bundle.case.plan_set_digest,
        "--cap-units",
        str(caps.max_units),
        "--cap-total-attempts",
        str(caps.max_total_attempts),
        "--cap-total-wire-body-bytes",
        str(caps.max_total_wire_body_bytes),
        "--cap-total-output-tokens",
        str(caps.max_total_output_tokens),
        "--cap-total-response-bytes",
        str(caps.max_total_response_bytes),
        "--cap-campaign-wall-clock-ms",
        str(caps.campaign_wall_clock_cap_ms),
        "--acknowledge-repository-assets-and-synthetic-code",
        REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
        "--state-dir",
        str(state_dir),
    ]


def _stat_mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)


def test_fixed_campaign_is_deterministic_multi_unit_and_inspection_is_safe() -> None:
    first = build_repository_synthetic_campaign_bundle()
    second = build_repository_synthetic_campaign_bundle()
    summary = build_campaign_inspection_summary(first)
    rendered = json.dumps(summary, sort_keys=True)

    assert first == second
    assert first.case.source_policy == "closed_package_assets_no_external_input"
    assert len(first.campaign.units) == first.caps.max_units == 4
    assert first.caps.max_total_attempts == 4
    assert [unit.plan.plan_id for unit in first.campaign.units] == sorted(
        unit.plan.plan_id for unit in first.campaign.units
    ) or [
        (unit.card.unit_id, unit.card.card_id, unit.plan.plan_id)
        for unit in first.campaign.units
    ] == sorted(
        (unit.card.unit_id, unit.card.card_id, unit.plan.plan_id)
        for unit in first.campaign.units
    )
    assert summary["mode"] == "inspect_only"
    assert summary["network_attempted"] is False
    assert summary["credential_accessed"] is False
    assert len(summary["plans"]) == 4  # type: ignore[arg-type]
    assert first.case.plan_set_digest in rendered
    for forbidden in (
        "fixtures/repository_synthetic_campaign.ets",
        "CampaignProbe",
        "repository synthetic old first",
        "repository synthetic new second",
        _API_KEY_SECRET,
        _RAW_SECRET,
        _MODEL_REASON_SECRET,
    ):
        assert forbidden not in rendered


def test_fixed_source_hash_is_an_independent_trust_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        campaign_live_smoke,
        "_FIXED_HEAD_CODE",
        campaign_live_smoke._FIXED_HEAD_CODE + "// changed",  # noqa: SLF001
    )
    with pytest.raises(CampaignSmokePreflightError) as rejected:
        build_repository_synthetic_campaign_bundle()
    assert rejected.value.code == "fixed_source_identity_mismatch"


def test_default_and_invalid_cli_are_preflight_exit_two_without_credentials_or_echo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_calls = 0

    def forbidden_factory() -> _Credential:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("credential factory must not run")

    assert main([], credential_provider_factory=forbidden_factory) == 2
    inspection = capsys.readouterr()
    assert json.loads(inspection.out)["mode"] == "inspect_only"
    assert inspection.err == ""
    assert factory_calls == 0

    arbitrary_secret = "ARBITRARY_SOURCE_INPUT_MUST_NOT_ECHO"
    assert (
        main(
            ["--source", arbitrary_secret],
            credential_provider_factory=forbidden_factory,
        )
        == 2
    )
    invalid = capsys.readouterr()
    assert json.loads(invalid.out)["error_code"] == "fixture_or_arguments_invalid"
    assert arbitrary_secret not in invalid.out + invalid.err
    assert factory_calls == 0


def test_real_transport_and_control_mismatches_fail_before_credentials_or_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    state_dir = tmp_path / "state"
    factory_calls = 0

    def forbidden_factory() -> _Credential:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("credential factory must not run")

    args = _execute_args(bundle, state_dir)
    assert main(args, credential_provider_factory=forbidden_factory) == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["error_code"] == "real_transport_not_explicitly_allowed"
    assert factory_calls == 0
    assert not state_dir.exists()

    mismatch = args.copy()
    mismatch[mismatch.index("--approve-plan-set-digest") + 1] = (
        "ai-tag-shadow-campaign-plan-set:sha256:" + "0" * 64
    )
    assert main(mismatch, test_transport=_transport()) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["error_code"] == "approved_plan_set_digest_mismatch"
    assert not state_dir.exists()


def test_approval_reservations_bind_campaign_plan_set_and_explicit_caps() -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    caps = bundle.caps
    approval = build_local_campaign_egress_approval(
        bundle,
        approved_campaign_id=bundle.case.campaign_id,
        approved_plan_set_digest=bundle.case.plan_set_digest,
        cap_units=caps.max_units,
        cap_total_attempts=caps.max_total_attempts,
        cap_total_wire_body_bytes=caps.max_total_wire_body_bytes,
        cap_total_output_tokens=caps.max_total_output_tokens,
        cap_total_response_bytes=caps.max_total_response_bytes,
        cap_campaign_wall_clock_ms=caps.campaign_wall_clock_cap_ms,
        acknowledgement=REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
    )
    reservations = build_local_campaign_plan_reservations(bundle, approval=approval)

    assert approval.campaign_id == bundle.case.campaign_id
    assert approval.plan_set_digest == bundle.case.plan_set_digest
    assert approval.caps_id == caps.caps_id
    assert len(reservations) == 4
    assert tuple(item.plan_id for item in reservations) == tuple(
        unit.plan.plan_id for unit in bundle.campaign.units
    )
    assert len({item.reservation_id for item in reservations}) == 4
    with pytest.raises(CampaignSmokePreflightError) as rejected:
        build_local_campaign_egress_approval(
            bundle,
            approved_campaign_id=bundle.case.campaign_id,
            approved_plan_set_digest=bundle.case.plan_set_digest,
            cap_units=caps.max_units,
            cap_total_attempts=caps.max_total_attempts - 1,
            cap_total_wire_body_bytes=caps.max_total_wire_body_bytes,
            cap_total_output_tokens=caps.max_total_output_tokens,
            cap_total_response_bytes=caps.max_total_response_bytes,
            cap_campaign_wall_clock_ms=caps.campaign_wall_clock_cap_ms,
            acknowledgement=REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
        )
    assert rejected.value.code == "cap_total_attempts_mismatch"


def test_valid_injected_campaign_is_canonical_single_attempt_and_replay_guarded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    state_dir = tmp_path / "campaign-state"
    transport = _transport()

    assert main(_execute_args(bundle, state_dir), test_transport=transport) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    expected_order = [unit.plan.plan_id for unit in bundle.campaign.units]
    assert transport.calls == expected_order
    assert len(transport.calls) == len(set(transport.calls)) == 4
    assert all(key.startswith("synthetic-injected-transport-") for key in transport.seen_keys)
    assert summary["counts"]["attempted_unit_count"] == 4
    assert summary["counts"]["valid_shape_count"] == 4
    assert summary["network_attempted"] is None
    assert summary["network_observation_scope"] == (
        "fixed_httpx_observation_or_unknown_for_injected_transport"
    )
    assert summary["production_qualified"] is False
    rendered = captured.out + captured.err
    for forbidden in (
        str(state_dir),
        "fixtures/repository_synthetic_campaign.ets",
        "CampaignProbe",
        "repository synthetic old first",
        "repository synthetic new second",
        _API_KEY_SECRET,
        _RAW_SECRET,
        _MODEL_REASON_SECRET,
    ):
        assert forbidden not in rendered

    markers = sorted(state_dir.glob("*.consumed.json"))
    assert len(markers) == 4
    assert _stat_mode(state_dir) == 0o700
    assert all(_stat_mode(marker) == 0o600 for marker in markers)
    marker_text = "".join(marker.read_text() for marker in markers)
    for forbidden in (
        "CampaignProbe",
        "repository synthetic",
        _API_KEY_SECRET,
        _RAW_SECRET,
        _MODEL_REASON_SECRET,
        str(state_dir),
    ):
        assert forbidden not in marker_text

    assert main(_execute_args(bundle, state_dir), test_transport=transport) == 2
    replay = json.loads(capsys.readouterr().out)
    assert replay["error_code"] == "campaign_plan_already_reserved"
    assert transport.calls == expected_order


def test_partial_attempt_returns_three_without_retry_or_sensitive_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    transport = _transport(invalid_ordinal=2)

    assert main(_execute_args(bundle, tmp_path / "state"), test_transport=transport) == 3
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert transport.calls == [unit.plan.plan_id for unit in bundle.campaign.units]
    assert len(transport.calls) == 4
    assert summary["counts"]["attempted_unit_count"] == 4
    assert summary["counts"]["valid_shape_count"] == 3
    assert summary["counts"]["inner_invalid_count"] == 1
    assert _MODEL_REASON_SECRET not in captured.out + captured.err
    assert _RAW_SECRET not in captured.out + captured.err


def test_summary_failure_uses_zero_attempt_result_instead_of_claiming_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    state_dir = tmp_path / "state"
    credential = _Credential(configured=False)
    args = _execute_args(bundle, state_dir)
    args.append("--allow-real-transport")

    def broken_summary(run: object) -> dict[str, object]:
        del run
        raise ValueError("synthetic summary failure")

    monkeypatch.setattr(campaign_live_smoke, "_build_run_summary", broken_summary)
    assert main(args, credential_provider_factory=lambda: credential) == 3

    summary = json.loads(capsys.readouterr().out)
    assert summary["error_code"] == "campaign_summary_invalid"
    assert summary["network_attempted"] is False
    assert credential.key_reads == 0
    assert not state_dir.exists()


def test_run_api_requires_explicit_real_or_injected_transport_mode(tmp_path: Path) -> None:
    bundle = build_repository_synthetic_campaign_bundle()
    caps = bundle.caps
    credential = _Credential()
    with pytest.raises(CampaignSmokePreflightError) as real_denied:
        run_repository_synthetic_campaign(
            approved_campaign_id=bundle.case.campaign_id,
            approved_plan_set_digest=bundle.case.plan_set_digest,
            cap_units=caps.max_units,
            cap_total_attempts=caps.max_total_attempts,
            cap_total_wire_body_bytes=caps.max_total_wire_body_bytes,
            cap_total_output_tokens=caps.max_total_output_tokens,
            cap_total_response_bytes=caps.max_total_response_bytes,
            cap_campaign_wall_clock_ms=caps.campaign_wall_clock_cap_ms,
            acknowledgement=REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            credential_provider=credential,
        )
    assert real_denied.value.code == "real_transport_not_explicitly_allowed"

    with pytest.raises(CampaignSmokePreflightError) as injected_denied:
        run_repository_synthetic_campaign(
            approved_campaign_id=bundle.case.campaign_id,
            approved_plan_set_digest=bundle.case.plan_set_digest,
            cap_units=caps.max_units,
            cap_total_attempts=caps.max_total_attempts,
            cap_total_wire_body_bytes=caps.max_total_wire_body_bytes,
            cap_total_output_tokens=caps.max_total_output_tokens,
            cap_total_response_bytes=caps.max_total_response_bytes,
            cap_campaign_wall_clock_ms=caps.campaign_wall_clock_cap_ms,
            acknowledgement=REPOSITORY_SYNTHETIC_CAMPAIGN_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            credential_provider=credential,
            transport=_transport(),
        )
    assert injected_denied.value.code == "injected_transport_not_explicitly_allowed"
