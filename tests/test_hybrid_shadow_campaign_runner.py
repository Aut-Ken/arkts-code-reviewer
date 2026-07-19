from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from arkts_code_reviewer.code_analysis import (
    ChangeAtomInput,
    ChangedFileInput,
    CodeAnalyzer,
    CodeSourceRef,
    CodeSourceSnapshot,
    ReviewUnitSpan,
    normalize_change_set,
)
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import DeepSeekHttpResponse
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    seal_ai_tag_shadow_dispatch_claims,
)
from arkts_code_reviewer.hybrid_analysis.shadow_campaign import (
    AITagShadowCampaignBundle,
    AITagShadowCampaignUnitArtifacts,
    build_ai_tag_shadow_campaign,
    build_ai_tag_shadow_campaign_evaluation_report,
    verify_ai_tag_shadow_campaign_evaluation_report,
)
from arkts_code_reviewer.hybrid_analysis.shadow_runtime import (
    AITagShadowAuthorizationGate,
    AITagShadowTrustedPlanInputs,
    DeepSeekShadowRunner,
)

_SYNTHETIC_TRANSPORT_TOKEN = (
    "synthetic-injected-transport-no-provider-credential"
)


def _snapshot(path: str, content: str, revision: str) -> CodeSourceSnapshot:
    content_hash = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    return CodeSourceSnapshot(
        source_ref=CodeSourceRef.create(
            repository="campaign-runner-test",
            revision=revision,
            path=path,
            content_hash=content_hash,
        ),
        content=content,
    )


def _campaign() -> AITagShadowCampaignBundle:
    path = "src/RunnerPage.ets"
    base = _snapshot(
        path,
        """@Entry
@Component
struct RunnerPage {
  first() {
    console.info(\"old first\")
  }
  second() {
    console.info(\"old second\")
  }
}
""",
        "base-revision",
    )
    head = _snapshot(
        path,
        """@Entry
@Component
struct RunnerPage {
  first() {
    console.info(\"new first\")
  }
  second() {
    console.info(\"new second\")
  }
}
""",
        "head-revision",
    )
    change_set = normalize_change_set(
        repository="campaign-runner-test",
        base_revision="base-revision",
        head_revision="head-revision",
        files=(
            ChangedFileInput(
                status="modified",
                old_path=path,
                new_path=path,
                old_snapshot=base,
                new_snapshot=head,
                atoms=(
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(5, 5),
                        new_span=ReviewUnitSpan(5, 5),
                        deleted_old_lines=(5,),
                        added_new_lines=(5,),
                    ),
                    ChangeAtomInput(
                        kind="replacement",
                        old_span=ReviewUnitSpan(8, 8),
                        new_span=ReviewUnitSpan(8, 8),
                        deleted_old_lines=(8,),
                        added_new_lines=(8,),
                    ),
                ),
            ),
        ),
    )
    snapshots = {
        base.source_ref.source_ref_id: base,
        head.source_ref.source_ref_id: head,
    }
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_change_set(change_set, snapshots)
    context_plan = analyzer.plan_context(
        analysis,
        source_snapshots=snapshots,
        code_context_budget=20_000,
    )
    selected_unit_ids = tuple(sorted(unit.unit_id for unit in analysis.review_units)[:2])
    assert len(selected_unit_ids) == 2
    return build_ai_tag_shadow_campaign(
        analysis_result=analysis,
        context_plan=context_plan,
        source_snapshots=snapshots,
        unit_ids=selected_unit_ids,
    )


def _valid_inner_content(unit: AITagShadowCampaignUnitArtifacts) -> str:
    judgments = [
        {
            "tag_id": contract.tag_id,
            "decision": "abstain",
            "evidence_lines": [],
            "reason_code": "insufficient_context",
            "reason": "Synthetic campaign Runner fixture.",
        }
        for contract in unit.request.tag_contract_views
    ]
    return json.dumps(
        {"judgments": judgments},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _provider_body(*, content: str, response_id: str) -> bytes:
    payload = {
        "id": response_id,
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
        "system_fingerprint": "fp-campaign-runner-fixture",
        "usage": {
            "completion_tokens": 50,
            "prompt_tokens": 100,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 25,
            "prompt_cache_miss_tokens": 75,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass
class _Credential:
    configured_checks: int = 0
    get_api_key_calls: int = 0

    @property
    def credential_scope_id(self) -> str:
        return "deepseek-credential-scope:sha256:" + "d" * 64

    def is_configured(self) -> bool:
        self.configured_checks += 1
        return True

    def get_api_key(self) -> str:
        self.get_api_key_calls += 1
        raise AssertionError("injected transport must not request a real credential")


@dataclass
class _EgressVerifier:
    expected_plan_id: str
    calls: int = 0

    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert approval_id.startswith("ai-egress-approval:sha256:")
        self.calls += 1


@dataclass
class _BudgetLedger:
    expected_plan_id: str
    calls: int = 0

    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        assert plan.plan_id == self.expected_plan_id
        assert plan.max_attempts == 1
        assert reservation_id.startswith("ai-budget-reservation:sha256:")
        self.calls += 1


@dataclass
class _InjectedTransport:
    response: DeepSeekHttpResponse
    calls: int = 0
    seen_api_keys: list[str] = field(default_factory=list)

    def send(
        self,
        plan: AITagShadowDispatchPlan,
        *,
        api_key: str,
    ) -> DeepSeekHttpResponse:
        assert plan.max_attempts == 1
        self.calls += 1
        self.seen_api_keys.append(api_key)
        return self.response


def _claims(
    plan: AITagShadowDispatchPlan,
    *,
    credential_scope_id: str,
    marker: str,
) -> AITagShadowDispatchClaims:
    return seal_ai_tag_shadow_dispatch_claims(
        {
            "schema_version": "ai-tag-shadow-dispatch-claims-v1",
            "plan_id": plan.plan_id,
            "trust_domain_id": "ai-shadow-trust-domain:sha256:" + marker * 64,
            "egress_approval_id": "ai-egress-approval:sha256:" + marker * 64,
            "budget_reservation_id": (
                "ai-budget-reservation:sha256:" + marker * 64
            ),
            "credential_scope_id": credential_scope_id,
            "egress_scope": "exact_wire_body_sha256",
            "budget_scope": "one_attempt_worst_case_reserved",
            "qualification": "references_require_runtime_verification",
        }
    )


def test_campaign_plans_run_through_injected_transport_and_existing_evaluator() -> None:
    bundle = _campaign()
    credential = _Credential()
    response_validations = {}
    transports: list[_InjectedTransport] = []
    egress_verifiers: list[_EgressVerifier] = []
    budget_ledgers: list[_BudgetLedger] = []

    for index, unit in enumerate(bundle.units):
        content = _valid_inner_content(unit) if index == 0 else "{"
        transport = _InjectedTransport(
            DeepSeekHttpResponse(
                status_code=200,
                body=_provider_body(
                    content=content,
                    response_id=f"chatcmpl-campaign-runner-{index}",
                ),
                retry_after_ms=None,
                latency_ms=10 + index,
            )
        )
        trusted_inputs = AITagShadowTrustedPlanInputs(
            envelope=unit.envelope,
            card=unit.card,
            context_policy=bundle.context_policy,
            max_output_tokens=unit.plan.wire_payload.max_tokens,
            wall_clock_timeout_ms=unit.plan.wall_clock_timeout_ms,
            max_response_bytes=unit.plan.max_response_bytes,
        )
        marker = str(index + 1)
        claims = _claims(
            unit.plan,
            credential_scope_id=credential.credential_scope_id,
            marker=marker,
        )
        egress_verifier = _EgressVerifier(unit.plan.plan_id)
        budget_ledger = _BudgetLedger(unit.plan.plan_id)
        gate = AITagShadowAuthorizationGate(
            trust_domain_id=claims.trust_domain_id,
            credential_provider=credential,
            trusted_plan_inputs=trusted_inputs,
            egress_verifier=egress_verifier,
            budget_ledger=budget_ledger,
        )
        capability = gate.authorize(plan=unit.plan, claims=claims)
        artifacts = DeepSeekShadowRunner(
            gate=gate,
            transport=transport,
        ).run(
            plan=unit.plan,
            claims=claims,
            capability=capability,
            envelope=unit.envelope,
        )

        assert artifacts.response_validation is not None
        assert artifacts.outer_response_diagnostic is None
        assert artifacts.attempt_receipt.attempt_ordinal == 1
        assert artifacts.observation.status == (
            "valid_shape" if index == 0 else "invalid_output"
        )
        response_validations[unit.plan.plan_id] = artifacts.response_validation
        transports.append(transport)
        egress_verifiers.append(egress_verifier)
        budget_ledgers.append(budget_ledger)

    report = build_ai_tag_shadow_campaign_evaluation_report(
        bundle,
        response_validations,
    )
    verify_ai_tag_shadow_campaign_evaluation_report(
        report,
        bundle,
        response_validations,
    )

    assert report.unit_count == 2
    assert report.valid_shape_unit_count == 1
    assert report.invalid_output_unit_count == 1
    assert report.unavailable_claim_unit_count == 0
    assert report.unverified_raw_unit_count == 2
    assert report.evidence_qualification_status == "not_qualified"
    assert report.production_qualified is False
    assert report.collection_scope == "caller_supplied_input_set_not_campaign_bound"

    assert credential.configured_checks == 2
    assert credential.get_api_key_calls == 0
    assert all(transport.calls == 1 for transport in transports)
    assert all(
        transport.seen_api_keys == [_SYNTHETIC_TRANSPORT_TOKEN]
        for transport in transports
    )
    assert all(verifier.calls == 1 for verifier in egress_verifiers)
    assert all(ledger.calls == 1 for ledger in budget_ledgers)
