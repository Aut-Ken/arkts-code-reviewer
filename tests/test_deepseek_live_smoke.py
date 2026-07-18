from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

import arkts_code_reviewer.hybrid_analysis.live_smoke as live_smoke
from arkts_code_reviewer.hybrid_analysis.builders import build_ai_tag_model_view
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import AITagDispatchEnvelopeBuilder
from arkts_code_reviewer.hybrid_analysis.live_smoke import (
    REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
    AtomicLocalOneAttemptBudgetLedger,
    OneShotExactBodyApprovalVerifier,
    SmokePreflightError,
    build_inspection_summary,
    build_local_exact_body_approval,
    build_local_one_attempt_reservation,
    build_repository_synthetic_smoke_bundle,
    build_run_summary,
    main,
    run_repository_synthetic_smoke,
)
from arkts_code_reviewer.hybrid_analysis.models import seal_review_unit_analysis_card
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchPlan,
    build_ai_tag_shadow_dispatch_plan,
)
from arkts_code_reviewer.hybrid_analysis.request_builder import FullTaxonomyRequestBuilder
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationError,
    AITagShadowTrustedPlanInputs,
)


@dataclass
class _Credential:
    configured: bool = True
    secret: str = "test-live-smoke-secret"
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
        return self.secret


@dataclass
class _Transport:
    outcome: DeepSeekHttpResponse | DeepSeekHttpTransportError
    calls: int = 0
    keys: list[str] = field(default_factory=list)

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.max_attempts == 1
        self.calls += 1
        self.keys.append(api_key)
        if isinstance(self.outcome, DeepSeekHttpTransportError):
            raise self.outcome
        return self.outcome


def _provider_body(*, invalid_inner: bool = False) -> bytes:
    bundle = build_repository_synthetic_smoke_bundle()
    judgments: list[dict[str, object]] = []
    for contract in bundle.request.tag_contract_views:
        if contract.tag_id == "has_timer" and not invalid_inner:
            judgment = {
                "tag_id": contract.tag_id,
                "decision": "positive",
                "evidence_lines": [2, 5],
                "reason_code": "direct_unit_semantic_evidence",
                "reason": "The Unit starts and clears a timer.",
            }
        elif contract.tag_id == "has_logging" and not invalid_inner:
            judgment = {
                "tag_id": contract.tag_id,
                "decision": "positive",
                "evidence_lines": [3],
                "reason_code": "direct_unit_semantic_evidence",
                "reason": "The Unit writes a log message.",
            }
        else:
            judgment = {
                "tag_id": contract.tag_id,
                "decision": "not_supported",
                "evidence_lines": [],
                "reason_code": "no_support_in_complete_view",
                "reason": None,
            }
        judgments.append(judgment)
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
            "id": "chatcmpl-repository-synthetic-smoke",
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
            "system_fingerprint": "fp-repository-synthetic-smoke",
            "usage": {
                "completion_tokens": 200,
                "prompt_tokens": 1_000,
                "total_tokens": 1_200,
                "prompt_cache_hit_tokens": 250,
                "prompt_cache_miss_tokens": 750,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _live_args(bundle, state_dir: Path) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        "--execute-live",
        "--approve-plan-id",
        bundle.plan.plan_id,
        "--approve-body-sha256",
        bundle.plan.wire_body_sha256,
        "--reserve-max-output-tokens",
        str(bundle.plan.wire_payload.max_tokens),
        "--acknowledge-repository-assets-and-synthetic-code",
        REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
        "--state-dir",
        str(state_dir),
    ]


def _authorization_reason(callable_) -> str:  # type: ignore[no-untyped-def]
    try:
        callable_()
    except AITagShadowAuthorizationError as exc:
        return exc.reason_code
    return "accepted"


def test_repository_synthetic_bundle_is_closed_deterministic_and_redacted() -> None:
    first = build_repository_synthetic_smoke_bundle()
    second = build_repository_synthetic_smoke_bundle()
    summary = build_inspection_summary(first)

    assert first == second
    assert first.manifest == second.manifest
    assert first.manifest.origin == "repository_authored_synthetic"
    assert first.manifest.data_classification == (
        "repository_contained_synthetic_no_user_code"
    )
    assert first.manifest.source_policy == "closed_package_assets_no_external_input"
    assert first.manifest.outbound_asset_scope == (
        "repository_prompt_taxonomy_and_synthetic_code"
    )
    assert first.manifest.prompt_hash == first.envelope.prompt.prompt_hash
    assert first.manifest.catalog_fingerprint.startswith(
        "ai-tag-contract-catalog:sha256:"
    )
    assert first.manifest.plan_id == first.plan.plan_id
    assert first.manifest.wire_body_sha256 == first.plan.wire_body_sha256
    assert first.plan.max_attempts == 1
    assert first.plan.execution_mode == "shadow_only_no_hybrid_no_retrieval"
    assert summary["network_attempted"] is False
    rendered = json.dumps(summary, ensure_ascii=False)
    assert "repository synthetic smoke tick" not in rendered
    assert "messages" not in rendered
    assert first.envelope.prompt.text not in rendered
    assert "prompt_text" not in rendered
    assert "wire_body_json" not in rendered


def test_repository_fixture_hash_is_a_separate_trust_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_smoke,
        "_REPOSITORY_SYNTHETIC_CODE",
        "startTimer(): void { /* tampered */ }",
    )
    with pytest.raises(SmokePreflightError) as raised:
        build_repository_synthetic_smoke_bundle()
    assert raised.value.code == "repository_fixture_identity_mismatch"


def test_runner_rebuilds_the_closed_bundle_before_any_control_or_send(tmp_path: Path) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    forged = replace(bundle, manifest=bundle.manifest.model_copy(update={"case_name": "forged"}))
    credential = _Credential()
    transport = _Transport(DeepSeekHttpResponse(200, _provider_body(), None, 1))

    with pytest.raises(SmokePreflightError) as raised:
        run_repository_synthetic_smoke(
            bundle=forged,
            approved_plan_id=bundle.plan.plan_id,
            approved_wire_body_sha256=bundle.plan.wire_body_sha256,
            reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
            acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
            state_dir=tmp_path / "state",
            credential_provider=credential,
            transport=transport,
        )
    assert raised.value.code == "repository_smoke_bundle_not_trusted"
    assert credential.configured_checks == 0
    assert transport.calls == 0


def test_local_controls_cannot_be_minted_for_an_arbitrary_code_plan() -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    card_payload = bundle.card.model_dump(mode="json", exclude={"card_id"})
    code = dict(card_payload["code"])
    code["text"] = "startTimer(): void {\n  const secret = 'private';\n  return;\n}\n\n"
    code["line_end"] = 6
    code["changed_line_numbers"] = [1, 2, 3, 4, 5, 6]
    card_payload["code"] = code
    forged_card = seal_review_unit_analysis_card(card_payload)
    forged_view = build_ai_tag_model_view(card=forged_card)
    request_builder = FullTaxonomyRequestBuilder.default()
    forged_request = request_builder.build(card=forged_card, model_view=forged_view)
    forged_envelope = AITagDispatchEnvelopeBuilder(request_builder=request_builder).build(
        card=forged_card,
        model_view=forged_view,
        request=forged_request,
    )
    forged_plan = build_ai_tag_shadow_dispatch_plan(
        envelope=forged_envelope,
        card=forged_card,
        context_policy=bundle.context_policy,
        max_output_tokens=bundle.plan.wire_payload.max_tokens,
        timeout_ms=bundle.plan.wall_clock_timeout_ms,
        max_response_bytes=bundle.plan.max_response_bytes,
    )
    forged_trusted_inputs = AITagShadowTrustedPlanInputs(
        envelope=forged_envelope,
        card=forged_card,
        context_policy=bundle.context_policy,
        max_output_tokens=forged_plan.wire_payload.max_tokens,
        wall_clock_timeout_ms=forged_plan.wall_clock_timeout_ms,
        max_response_bytes=forged_plan.max_response_bytes,
    )
    forged_bundle = replace(
        bundle,
        card=forged_card,
        model_view=forged_view,
        request=forged_request,
        envelope=forged_envelope,
        plan=forged_plan,
        trusted_plan_inputs=forged_trusted_inputs,
    )

    with pytest.raises(SmokePreflightError) as approval_rejected:
        build_local_exact_body_approval(
            forged_bundle,
            approved_plan_id=forged_plan.plan_id,
            approved_wire_body_sha256=forged_plan.wire_body_sha256,
            acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
        )
    assert approval_rejected.value.code == "repository_smoke_bundle_not_trusted"

    with pytest.raises(SmokePreflightError) as reservation_rejected:
        build_local_one_attempt_reservation(
            forged_bundle,
            reserved_max_output_tokens=forged_plan.wire_payload.max_tokens,
        )
    assert reservation_rejected.value.code == "repository_smoke_bundle_not_trusted"


def test_exact_approval_and_attempt_cap_are_content_addressed() -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    approval = build_local_exact_body_approval(
        bundle,
        approved_plan_id=bundle.plan.plan_id,
        approved_wire_body_sha256=bundle.plan.wire_body_sha256,
        acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
    )
    reservation = build_local_one_attempt_reservation(
        bundle,
        reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
    )

    assert approval.approval_id.startswith("ai-egress-approval:sha256:")
    assert reservation.reservation_id.startswith("ai-budget-reservation:sha256:")
    assert approval.plan_id == reservation.plan_id == bundle.plan.plan_id
    assert approval.qualification == (
        "local_operator_control_not_deployment_compliance_approval"
    )
    assert reservation.qualification == (
        "local_attempt_cap_not_currency_or_provider_budget"
    )

    with pytest.raises(SmokePreflightError) as wrong_plan:
        build_local_exact_body_approval(
            bundle,
            approved_plan_id="ai-tag-shadow-plan:sha256:" + "0" * 64,
            approved_wire_body_sha256=bundle.plan.wire_body_sha256,
            acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
        )
    assert wrong_plan.value.code == "approved_plan_id_mismatch"

    with pytest.raises(SmokePreflightError) as wrong_budget:
        build_local_one_attempt_reservation(
            bundle,
            reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens + 1,
        )
    assert wrong_budget.value.code == "reserved_max_output_tokens_mismatch"


def test_approval_and_budget_controls_are_one_shot_and_concurrency_safe(
    tmp_path: Path,
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    approval = build_local_exact_body_approval(
        bundle,
        approved_plan_id=bundle.plan.plan_id,
        approved_wire_body_sha256=bundle.plan.wire_body_sha256,
        acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
    )
    verifier = OneShotExactBodyApprovalVerifier(
        manifest=bundle.manifest,
        approval=approval,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        approval_results = list(
            executor.map(
                lambda _: _authorization_reason(
                    lambda: verifier.verify_exact_body_egress(
                        plan=bundle.plan,
                        approval_id=approval.approval_id,
                    )
                ),
                range(2),
            )
        )
    assert sorted(approval_results) == ["accepted", "egress_not_approved"]

    reservation = build_local_one_attempt_reservation(
        bundle,
        reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
    )
    ledger = AtomicLocalOneAttemptBudgetLedger(
        manifest=bundle.manifest,
        reservation=reservation,
        state_dir=tmp_path / "state",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        budget_results = list(
            executor.map(
                lambda _: _authorization_reason(
                    lambda: ledger.consume_one_attempt_reservation(
                        plan=bundle.plan,
                        reservation_id=reservation.reservation_id,
                    )
                ),
                range(2),
            )
        )
    assert sorted(budget_results) == ["accepted", "budget_not_reserved"]
    assert ledger.marker_path.is_file()
    assert stat_mode(ledger.marker_path) == 0o600


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


def test_default_cli_is_inspect_only_and_never_reads_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def forbidden_factory() -> _Credential:
        nonlocal calls
        calls += 1
        raise AssertionError("dry-run must not construct a credential provider")

    assert main([], credential_provider_factory=forbidden_factory) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "inspect_only"
    assert summary["network_attempted"] is False
    assert calls == 0


def test_live_flags_without_execute_still_never_read_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()

    def forbidden_factory() -> _Credential:
        raise AssertionError("without --execute-live the credential is unreachable")

    args = _live_args(bundle, tmp_path / "state")
    args.remove("--execute-live")
    assert main(args, credential_provider_factory=forbidden_factory) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "inspect_only"


@pytest.mark.parametrize(
    ("args", "error_code"),
    [
        (["--execute-live"], "live_controls_incomplete"),
        (
            [
                "--execute-live",
                "--approve-plan-id",
                "ai-tag-shadow-plan:sha256:" + "0" * 64,
                "--approve-body-sha256",
                "sha256:" + "0" * 64,
                "--reserve-max-output-tokens",
                "4096",
                "--acknowledge-repository-assets-and-synthetic-code",
                REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
                "--state-dir",
                "/tmp/not-used-because-plan-mismatch",
            ],
            "approved_plan_id_mismatch",
        ),
    ],
)
def test_live_preflight_failures_are_redacted_and_do_not_send(
    args: list[str],
    error_code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _Transport(DeepSeekHttpResponse(200, _provider_body(), None, 1))
    assert (
        main(
            args,
            credential_provider_factory=_Credential,
            test_transport=transport,
        )
        == 2
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["error_code"] == error_code
    assert summary["network_attempted"] is False
    assert transport.calls == 0


def test_missing_credential_does_not_consume_budget_or_send(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    credential = _Credential(configured=False)
    transport = _Transport(DeepSeekHttpResponse(200, _provider_body(), None, 1))
    state_dir = tmp_path / "state"

    assert (
        main(
            _live_args(bundle, state_dir),
            credential_provider_factory=lambda: credential,
            test_transport=transport,
        )
        == 2
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["error_code"] == "credential_not_configured"
    assert transport.calls == 0
    assert credential.configured_checks == 1
    assert credential.key_reads == 0
    assert not state_dir.exists()


def test_valid_injected_smoke_is_redacted_and_replay_is_blocked(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    credential = _Credential()
    raw_body = _provider_body()
    transport = _Transport(DeepSeekHttpResponse(200, raw_body, None, 17))
    state_dir = tmp_path / "state"
    args = _live_args(bundle, state_dir)

    assert (
        main(
            args,
            credential_provider_factory=lambda: credential,
            test_transport=transport,
        )
        == 0
    )
    first_output = capsys.readouterr().out
    first = json.loads(first_output)
    assert first["status"] == "valid_shape"
    assert first["judgment_count"] == 24
    assert first["decision_counts"] == {"not_supported": 22, "positive": 2}
    assert first["network_attempted"] is None
    assert first["transport_evidence"] == "injected_untrusted_transport"
    assert first["raw_response_retained"] is False
    assert first["outbound_asset_scope"] == (
        "repository_prompt_taxonomy_and_synthetic_code"
    )
    assert first["prompt_hash"] == bundle.manifest.prompt_hash
    assert first["catalog_fingerprint"] == bundle.manifest.catalog_fingerprint
    assert transport.calls == 1
    assert credential.key_reads == 0

    secret_material = (
        credential.secret,
        "repository synthetic smoke tick",
        "The Unit starts and clears a timer.",
        raw_body.decode(),
        "wire_body_json",
    )
    marker_text = "\n".join(path.read_text() for path in state_dir.iterdir())
    for forbidden in secret_material:
        assert forbidden not in first_output
        assert forbidden not in marker_text
    assert stat_mode(state_dir) == 0o700

    assert (
        main(
            args,
            credential_provider_factory=lambda: credential,
            test_transport=transport,
        )
        == 2
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["error_code"] == "budget_not_reserved"
    assert replay["network_attempted"] is False
    assert transport.calls == 1


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (
            DeepSeekHttpResponse(503, b'{"secret_provider_error":"do-not-echo"}', None, 7),
            "provider_server_error",
        ),
        (DeepSeekHttpResponse(200, b'{"outer":"invalid-do-not-echo"}', None, 8), "invalid_output"),
        (
            DeepSeekHttpTransportError("provider_timeout", latency_ms=9),
            "provider_timeout",
        ),
    ],
)
def test_attempt_failures_are_single_shot_and_do_not_echo_raw_bodies(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: DeepSeekHttpResponse | DeepSeekHttpTransportError,
    expected_status: str,
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    transport = _Transport(outcome)
    state_dir = tmp_path / expected_status

    assert (
        main(
            _live_args(bundle, state_dir),
            credential_provider_factory=_Credential,
            test_transport=transport,
        )
        == 3
    )
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["status"] == expected_status
    assert summary["raw_response_retained"] is False
    assert transport.calls == 1
    assert "do-not-echo" not in output


def test_inner_invalid_response_remains_non_formal_and_redacted(tmp_path: Path) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    raw_body = _provider_body(invalid_inner=True)
    run = run_repository_synthetic_smoke(
        bundle=bundle,
        approved_plan_id=bundle.plan.plan_id,
        approved_wire_body_sha256=bundle.plan.wire_body_sha256,
        reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
        acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
        state_dir=tmp_path / "state",
        credential_provider=_Credential(),
        transport=_Transport(DeepSeekHttpResponse(200, raw_body, None, 11)),
    )
    summary = build_run_summary(run)

    assert run.artifacts.observation.status == "invalid_output"
    assert run.artifacts.response_validation is not None
    assert run.artifacts.response_validation.judgments == ()
    assert summary["status"] == "invalid_output"
    assert summary["judgment_count"] == 0
    assert summary["qualification"] == (
        "local_unattested_shadow_smoke_not_formal_or_quality_evidence"
    )
    assert raw_body.decode() not in json.dumps(summary)


def test_unsafe_existing_state_directory_fails_closed_without_send(
    tmp_path: Path,
) -> None:
    bundle = build_repository_synthetic_smoke_bundle()
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o755)
    os.chmod(state_dir, 0o755)
    transport = _Transport(DeepSeekHttpResponse(200, _provider_body(), None, 1))

    with pytest.raises(AITagShadowAuthorizationError) as raised:
        run_repository_synthetic_smoke(
            bundle=bundle,
            approved_plan_id=bundle.plan.plan_id,
            approved_wire_body_sha256=bundle.plan.wire_body_sha256,
            reserved_max_output_tokens=bundle.plan.wire_payload.max_tokens,
            acknowledgement=REPOSITORY_SYNTHETIC_ACKNOWLEDGEMENT,
            state_dir=state_dir,
            credential_provider=_Credential(),
            transport=transport,
        )
    assert raised.value.reason_code == "budget_not_reserved"
    assert transport.calls == 0
