from __future__ import annotations

import hashlib
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol

from arkts_code_reviewer.hybrid_analysis.builders import AnalysisContextPolicy
from arkts_code_reviewer.hybrid_analysis.deepseek_adapter import (
    DeepSeekCredentialProvider,
    DeepSeekCredentialUnavailableError,
    DeepSeekHttpResponse,
    DeepSeekHttpTransportError,
    DeepSeekOuterResponseDiagnostic,
    DeepSeekOuterResponseError,
    DeepSeekShadowHttpTransport,
    _HttpxDeepSeekShadowTransport,
    observe_deepseek_chat_completion,
    parse_deepseek_chat_completion,
    verify_deepseek_observed_provider_response_receipt,
)
from arkts_code_reviewer.hybrid_analysis.dispatch import VerifiedAITagDispatchEnvelope
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagResponseValidation,
    validate_ai_tag_completion,
    verify_ai_tag_response_validation,
)
from arkts_code_reviewer.hybrid_analysis.models import ReviewUnitAnalysisCard
from arkts_code_reviewer.hybrid_analysis.provider_receipts import (
    AI_TAG_DISPATCH_ATTEMPT_RECEIPT_SCHEMA_VERSION,
    AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION,
    AITagDispatchAttemptReceipt,
    AITagObservedProviderResponseReceipt,
    AITagShadowDispatchClaims,
    AITagShadowDispatchPlan,
    AITagShadowExecutionObservationV2,
    build_ai_tag_shadow_dispatch_plan,
    seal_ai_tag_dispatch_attempt_receipt,
    seal_ai_tag_shadow_execution_observation_v2,
    verify_ai_tag_dispatch_attempt_receipt,
    verify_ai_tag_shadow_dispatch_plan,
)

_TRUST_DOMAIN_PATTERN = re.compile(r"^ai-shadow-trust-domain:sha256:[0-9a-f]{64}$")
_SYNTHETIC_INJECTED_TRANSPORT_API_KEY = "synthetic-injected-transport-no-provider-credential"


class AITagEgressApprovalVerifier(Protocol):
    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None: ...


class AITagBudgetReservationLedger(Protocol):
    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None: ...


@dataclass(frozen=True, repr=False)
class AITagShadowTrustedPlanInputs:
    """Deployment-owned roots used to rebuild a Plan before authorization."""

    envelope: VerifiedAITagDispatchEnvelope
    card: ReviewUnitAnalysisCard
    context_policy: AnalysisContextPolicy
    max_output_tokens: int
    wall_clock_timeout_ms: int
    max_response_bytes: int

    def __post_init__(self) -> None:
        envelope = VerifiedAITagDispatchEnvelope.model_validate(
            self.envelope.model_dump(mode="json")
        )
        card = ReviewUnitAnalysisCard.model_validate(self.card.model_dump(mode="json"))
        if not isinstance(self.context_policy, AnalysisContextPolicy):
            raise TypeError("trusted Plan context policy has an unsupported type")
        object.__setattr__(self, "envelope", envelope)
        object.__setattr__(self, "card", card)
        build_ai_tag_shadow_dispatch_plan(
            envelope=envelope,
            card=card,
            context_policy=self.context_policy,
            max_output_tokens=self.max_output_tokens,
            timeout_ms=self.wall_clock_timeout_ms,
            max_response_bytes=self.max_response_bytes,
        )

    def __repr__(self) -> str:
        return "AITagShadowTrustedPlanInputs(<deployment-owned-roots>)"

    def verify_plan(self, plan: AITagShadowDispatchPlan) -> None:
        verify_ai_tag_shadow_dispatch_plan(
            plan,
            envelope=self.envelope,
            card=self.card,
            context_policy=self.context_policy,
            trusted_max_output_tokens=self.max_output_tokens,
            trusted_timeout_ms=self.wall_clock_timeout_ms,
            trusted_max_response_bytes=self.max_response_bytes,
        )


class DenyAllAITagEgressApprovalVerifier:
    def verify_exact_body_egress(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        approval_id: str,
    ) -> None:
        del plan, approval_id
        raise AITagShadowAuthorizationError("egress_not_approved")


class DenyAllAITagBudgetReservationLedger:
    def consume_one_attempt_reservation(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        reservation_id: str,
    ) -> None:
        del plan, reservation_id
        raise AITagShadowAuthorizationError("budget_not_reserved")


AuthorizationFailureReason = Literal[
    "plan_not_trusted",
    "claims_mismatch",
    "credential_not_configured",
    "egress_not_approved",
    "budget_not_reserved",
    "capability_invalid",
    "capability_replayed",
]


class AITagShadowAuthorizationError(RuntimeError):
    def __init__(self, reason_code: AuthorizationFailureReason) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


_CAPABILITY_CONSTRUCTION_TOKEN = object()


class AITagShadowDispatchCapability:
    """Opaque process-local, single-use authority. It is intentionally not serializable."""

    __slots__ = (
        "_credential_scope_id",
        "_claims_id",
        "_gate_nonce",
        "_plan_id",
        "_trust_domain_id",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        gate_nonce: str,
        plan_id: str,
        trust_domain_id: str,
        credential_scope_id: str,
        claims_id: str,
    ) -> None:
        if construction_token is not _CAPABILITY_CONSTRUCTION_TOKEN:
            raise TypeError("dispatch capabilities can only be issued by an authorization gate")
        self._gate_nonce = gate_nonce
        self._plan_id = plan_id
        self._trust_domain_id = trust_domain_id
        self._credential_scope_id = credential_scope_id
        self._claims_id = claims_id

    def __repr__(self) -> str:
        return "AITagShadowDispatchCapability(<opaque-single-use>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("dispatch capabilities are not serializable")


class AITagShadowAuthorizationGate:
    """Runtime trust root. Serializable claims alone can never pass this gate."""

    def __init__(
        self,
        *,
        trust_domain_id: str,
        credential_provider: DeepSeekCredentialProvider,
        trusted_plan_inputs: AITagShadowTrustedPlanInputs,
        egress_verifier: AITagEgressApprovalVerifier | None = None,
        budget_ledger: AITagBudgetReservationLedger | None = None,
    ) -> None:
        if _TRUST_DOMAIN_PATTERN.fullmatch(trust_domain_id) is None:
            raise ValueError("invalid AI Tag shadow trust-domain identity")
        if not isinstance(trusted_plan_inputs, AITagShadowTrustedPlanInputs):
            raise TypeError("AI Tag shadow Gate requires trusted Plan inputs")
        self.trust_domain_id = trust_domain_id
        self._trusted_plan_inputs = trusted_plan_inputs
        self._credential_provider = credential_provider
        self._egress_verifier = (
            DenyAllAITagEgressApprovalVerifier() if egress_verifier is None else egress_verifier
        )
        self._budget_ledger = (
            DenyAllAITagBudgetReservationLedger() if budget_ledger is None else budget_ledger
        )
        self._issued_nonces: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    @property
    def trusted_plan_inputs(self) -> AITagShadowTrustedPlanInputs:
        return self._trusted_plan_inputs

    def _verify_trusted_plan(self, plan: AITagShadowDispatchPlan) -> None:
        try:
            self._trusted_plan_inputs.verify_plan(plan)
        except ValueError:
            raise AITagShadowAuthorizationError("plan_not_trusted") from None

    def authorize(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
    ) -> AITagShadowDispatchCapability:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        claims = AITagShadowDispatchClaims.model_validate(claims.model_dump(mode="json"))
        self._verify_trusted_plan(plan)
        if (
            claims.plan_id != plan.plan_id
            or claims.trust_domain_id != self.trust_domain_id
            or claims.credential_scope_id != self._credential_provider.credential_scope_id
        ):
            raise AITagShadowAuthorizationError("claims_mismatch")
        self._egress_verifier.verify_exact_body_egress(
            plan=plan,
            approval_id=claims.egress_approval_id,
        )
        if not self._credential_provider.is_configured():
            raise AITagShadowAuthorizationError("credential_not_configured")
        self._budget_ledger.consume_one_attempt_reservation(
            plan=plan,
            reservation_id=claims.budget_reservation_id,
        )
        nonce = secrets.token_hex(32)
        with self._lock:
            self._issued_nonces[nonce] = (plan.plan_id, claims.claims_id)
        return AITagShadowDispatchCapability(
            construction_token=_CAPABILITY_CONSTRUCTION_TOKEN,
            gate_nonce=nonce,
            plan_id=plan.plan_id,
            trust_domain_id=self.trust_domain_id,
            credential_scope_id=self._credential_provider.credential_scope_id,
            claims_id=claims.claims_id,
        )

    def _consume_capability(
        self,
        capability: AITagShadowDispatchCapability,
        *,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
    ) -> None:
        if not isinstance(capability, AITagShadowDispatchCapability):
            raise AITagShadowAuthorizationError("capability_invalid")
        if (
            capability._trust_domain_id != self.trust_domain_id
            or capability._plan_id != plan.plan_id
            or capability._credential_scope_id != self._credential_provider.credential_scope_id
            or capability._claims_id != claims.claims_id
        ):
            raise AITagShadowAuthorizationError("capability_invalid")
        with self._lock:
            issued_binding = self._issued_nonces.pop(capability._gate_nonce, None)
        if issued_binding is None:
            raise AITagShadowAuthorizationError("capability_replayed")
        if issued_binding != (plan.plan_id, claims.claims_id):
            raise AITagShadowAuthorizationError("capability_invalid")

    def dispatch_once(
        self,
        *,
        capability: AITagShadowDispatchCapability,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
        transport: DeepSeekShadowHttpTransport,
    ) -> DeepSeekHttpResponse:
        """Consume one capability and keep real credentials inside the fixed boundary."""

        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        claims = AITagShadowDispatchClaims.model_validate(claims.model_dump(mode="json"))
        self._verify_trusted_plan(plan)
        if (
            claims.plan_id != plan.plan_id
            or claims.trust_domain_id != self.trust_domain_id
            or claims.credential_scope_id != self._credential_provider.credential_scope_id
        ):
            raise AITagShadowAuthorizationError("claims_mismatch")
        self._consume_capability(
            capability,
            plan=plan,
            claims=claims,
        )
        if (
            type(transport) is _HttpxDeepSeekShadowTransport
            and transport.establishes_fixed_tls_network_evidence
        ):
            try:
                api_key = self._credential_provider.get_api_key()
            except DeepSeekCredentialUnavailableError:
                raise AITagShadowAuthorizationError("credential_not_configured") from None
            return transport.send(
                plan,
                api_key=api_key,
            )
        return transport.send(
            plan,
            api_key=_SYNTHETIC_INJECTED_TRANSPORT_API_KEY,
        )


@dataclass(frozen=True)
class AITagShadowRunArtifacts:
    attempt_receipt: AITagDispatchAttemptReceipt
    provider_response_receipt: AITagObservedProviderResponseReceipt | None
    response_validation: AITagResponseValidation | None
    outer_response_diagnostic: DeepSeekOuterResponseDiagnostic | None
    observation: AITagShadowExecutionObservationV2


class DeepSeekShadowRunner:
    """Execute exactly one authorized shadow attempt and never produce a formal Result."""

    def __init__(
        self,
        *,
        gate: AITagShadowAuthorizationGate,
        transport: DeepSeekShadowHttpTransport | None = None,
    ) -> None:
        self._gate = gate
        self._transport = _HttpxDeepSeekShadowTransport() if transport is None else transport

    def run(
        self,
        *,
        plan: AITagShadowDispatchPlan,
        claims: AITagShadowDispatchClaims,
        capability: AITagShadowDispatchCapability,
        envelope: VerifiedAITagDispatchEnvelope,
    ) -> AITagShadowRunArtifacts:
        plan = AITagShadowDispatchPlan.model_validate(plan.model_dump(mode="json"))
        claims = AITagShadowDispatchClaims.model_validate(claims.model_dump(mode="json"))
        envelope = VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
        self._gate._verify_trusted_plan(plan)  # noqa: SLF001
        _verify_plan_envelope(plan=plan, envelope=envelope)
        if claims.plan_id != plan.plan_id or claims.trust_domain_id != self._gate.trust_domain_id:
            raise AITagShadowAuthorizationError("claims_mismatch")
        transport_evidence: Literal[
            "httpx_tls_fixed_endpoint",
            "injected_untrusted_transport",
        ] = (
            "httpx_tls_fixed_endpoint"
            if type(self._transport) is _HttpxDeepSeekShadowTransport
            and self._transport.establishes_fixed_tls_network_evidence
            else "injected_untrusted_transport"
        )
        try:
            response = self._gate.dispatch_once(
                capability=capability,
                plan=plan,
                claims=claims,
                transport=self._transport,
            )
        except DeepSeekHttpTransportError as exc:
            attempt = _attempt_failure_receipt(
                plan=plan,
                claims=claims,
                failure=exc,
                transport_evidence=transport_evidence,
            )
            observation = _observation(
                plan=plan,
                claims=claims,
                attempt=attempt,
                response_receipt=None,
                validation=None,
                status=exc.kind,
                reason_code=exc.kind,
            )
            return _finalize_run_artifacts(
                AITagShadowRunArtifacts(
                    attempt_receipt=attempt,
                    provider_response_receipt=None,
                    response_validation=None,
                    outer_response_diagnostic=None,
                    observation=observation,
                ),
                plan=plan,
                claims=claims,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                raw_response_body=None,
            )
        if not isinstance(response, DeepSeekHttpResponse):
            failure = DeepSeekHttpTransportError(
                "provider_transport_error",
                latency_ms=0,
            )
            attempt = _attempt_failure_receipt(
                plan=plan,
                claims=claims,
                failure=failure,
                transport_evidence=transport_evidence,
            )
            observation = _observation(
                plan=plan,
                claims=claims,
                attempt=attempt,
                response_receipt=None,
                validation=None,
                status=failure.kind,
                reason_code=failure.kind,
            )
            return _finalize_run_artifacts(
                AITagShadowRunArtifacts(
                    attempt_receipt=attempt,
                    provider_response_receipt=None,
                    response_validation=None,
                    outer_response_diagnostic=None,
                    observation=observation,
                ),
                plan=plan,
                claims=claims,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                raw_response_body=None,
            )
        if len(response.body) > plan.max_response_bytes:
            failure = DeepSeekHttpTransportError(
                "provider_response_too_large",
                latency_ms=response.latency_ms,
            )
            attempt = _attempt_failure_receipt(
                plan=plan,
                claims=claims,
                failure=failure,
                transport_evidence=transport_evidence,
            )
            observation = _observation(
                plan=plan,
                claims=claims,
                attempt=attempt,
                response_receipt=None,
                validation=None,
                status=failure.kind,
                reason_code=failure.kind,
            )
            return _finalize_run_artifacts(
                AITagShadowRunArtifacts(
                    attempt_receipt=attempt,
                    provider_response_receipt=None,
                    response_validation=None,
                    outer_response_diagnostic=None,
                    observation=observation,
                ),
                plan=plan,
                claims=claims,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                raw_response_body=None,
            )
        attempt = _attempt_response_receipt(
            plan=plan,
            claims=claims,
            response=response,
            transport_evidence=transport_evidence,
        )
        if response.status_code != 200:
            status, reason = _http_failure(response.status_code)
            observation = _observation(
                plan=plan,
                claims=claims,
                attempt=attempt,
                response_receipt=None,
                validation=None,
                status=status,
                reason_code=reason,
            )
            return _finalize_run_artifacts(
                AITagShadowRunArtifacts(
                    attempt_receipt=attempt,
                    provider_response_receipt=None,
                    response_validation=None,
                    outer_response_diagnostic=None,
                    observation=observation,
                ),
                plan=plan,
                claims=claims,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                raw_response_body=response.body,
            )
        try:
            parsed, response_receipt = observe_deepseek_chat_completion(
                plan=plan,
                attempt_receipt=attempt,
                raw_body=response.body,
            )
        except DeepSeekOuterResponseError as exc:
            outer_diagnostic = exc.diagnostic
            observation = _observation(
                plan=plan,
                claims=claims,
                attempt=attempt,
                response_receipt=None,
                validation=None,
                outer_diagnostic=outer_diagnostic,
                status="invalid_output",
                reason_code="provider_outer_contract_invalid",
            )
            return _finalize_run_artifacts(
                AITagShadowRunArtifacts(
                    attempt_receipt=attempt,
                    provider_response_receipt=None,
                    response_validation=None,
                    outer_response_diagnostic=outer_diagnostic,
                    observation=observation,
                ),
                plan=plan,
                claims=claims,
                trusted_plan_inputs=self._gate.trusted_plan_inputs,
                raw_response_body=response.body,
            )
        verify_deepseek_observed_provider_response_receipt(
            response_receipt,
            plan=plan,
            attempt_receipt=attempt,
            raw_body=response.body,
        )
        validation = validate_ai_tag_completion(
            envelope,
            parsed.raw_completion,
        )
        observation = _observation(
            plan=plan,
            claims=claims,
            attempt=attempt,
            response_receipt=response_receipt,
            validation=validation,
            status=validation.status,
            reason_code=validation.reason_code,
        )
        return _finalize_run_artifacts(
            AITagShadowRunArtifacts(
                attempt_receipt=attempt,
                provider_response_receipt=response_receipt,
                response_validation=validation,
                outer_response_diagnostic=None,
                observation=observation,
            ),
            plan=plan,
            claims=claims,
            trusted_plan_inputs=self._gate.trusted_plan_inputs,
            raw_response_body=response.body,
        )


def _verify_plan_envelope(
    *,
    plan: AITagShadowDispatchPlan,
    envelope: VerifiedAITagDispatchEnvelope,
) -> None:
    base = envelope.wire_payload
    shadow = plan.wire_payload
    if (
        plan.envelope_id != envelope.envelope_id
        or plan.request_id != envelope.analysis_request.request_id
        or plan.card_id != envelope.analysis_request.card_id
        or plan.model_view_id != envelope.model_view.model_view_id
        or plan.shadow_provider_policy.upstream_render_policy_fingerprint
        != envelope.model_policy.model_policy_fingerprint
        or plan.shadow_provider_policy.upstream_dispatch_mode_required
        != envelope.model_policy.dispatch_mode
        or shadow.model != base.model
        or shadow.messages != base.messages
        or shadow.thinking != base.thinking
        or shadow.temperature != base.temperature
        or shadow.stream != base.stream
        or shadow.tool_choice != base.tool_choice
        or shadow.response_format != base.response_format
    ):
        raise AITagShadowAuthorizationError("claims_mismatch")


def _base_attempt_payload(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    transport_evidence: Literal[
        "httpx_tls_fixed_endpoint",
        "injected_untrusted_transport",
    ],
) -> dict[str, object]:
    is_live_httpx = transport_evidence == "httpx_tls_fixed_endpoint"
    return {
        "schema_version": AI_TAG_DISPATCH_ATTEMPT_RECEIPT_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "envelope_id": plan.envelope_id,
        "request_id": plan.request_id,
        "claims_id": claims.claims_id,
        "trust_domain_id": claims.trust_domain_id,
        "egress_approval_id": claims.egress_approval_id,
        "budget_reservation_id": claims.budget_reservation_id,
        "credential_scope_id": claims.credential_scope_id,
        "wire_body_sha256": plan.wire_body_sha256,
        "endpoint_url": plan.endpoint_url,
        "http_method": plan.http_method,
        "attempt_ordinal": 1,
        "tls_verify": plan.tls_verify,
        "follow_redirects": plan.follow_redirects,
        "trust_env": plan.trust_env,
        "transport_evidence": transport_evidence,
        "network_observation": (
            "observed_by_fixed_httpx_transport"
            if is_live_httpx
            else "not_established_by_injected_transport"
        ),
        "qualification": (
            "local_runtime_observation_not_provider_signature"
            if is_live_httpx
            else "synthetic_or_untrusted_transport_not_network_evidence"
        ),
    }


def _attempt_failure_receipt(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    failure: DeepSeekHttpTransportError,
    transport_evidence: Literal[
        "httpx_tls_fixed_endpoint",
        "injected_untrusted_transport",
    ],
) -> AITagDispatchAttemptReceipt:
    payload = _base_attempt_payload(
        plan=plan,
        claims=claims,
        transport_evidence=transport_evidence,
    )
    payload.update(
        {
            "transport_status": failure.kind,
            "http_status": None,
            "response_body_sha256": None,
            "response_body_size_bytes": None,
            "retry_after_ms": None,
            "latency_ms": failure.latency_ms,
        }
    )
    return seal_ai_tag_dispatch_attempt_receipt(payload)


def _attempt_response_receipt(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    response: DeepSeekHttpResponse,
    transport_evidence: Literal[
        "httpx_tls_fixed_endpoint",
        "injected_untrusted_transport",
    ],
) -> AITagDispatchAttemptReceipt:
    payload = _base_attempt_payload(
        plan=plan,
        claims=claims,
        transport_evidence=transport_evidence,
    )
    payload.update(
        {
            "transport_status": "response_received",
            "http_status": response.status_code,
            "response_body_sha256": ("sha256:" + hashlib.sha256(response.body).hexdigest()),
            "response_body_size_bytes": len(response.body),
            "retry_after_ms": response.retry_after_ms,
            "latency_ms": response.latency_ms,
        }
    )
    return seal_ai_tag_dispatch_attempt_receipt(payload)


def _http_failure(
    status_code: int,
) -> tuple[
    Literal[
        "provider_client_error",
        "provider_rate_limited",
        "provider_server_error",
    ],
    str,
]:
    if status_code == 429:
        return "provider_rate_limited", "provider_rate_limited"
    if 500 <= status_code <= 599:
        return "provider_server_error", "provider_server_error"
    return "provider_client_error", "provider_client_error"


def _observation(
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    attempt: AITagDispatchAttemptReceipt,
    response_receipt: AITagObservedProviderResponseReceipt | None,
    validation: AITagResponseValidation | None,
    outer_diagnostic: DeepSeekOuterResponseDiagnostic | None = None,
    status: str,
    reason_code: str,
) -> AITagShadowExecutionObservationV2:
    return seal_ai_tag_shadow_execution_observation_v2(
        {
            "schema_version": AI_TAG_SHADOW_EXECUTION_OBSERVATION_V2_SCHEMA_VERSION,
            "plan_id": plan.plan_id,
            "claims_id": claims.claims_id,
            "attempt_receipt_id": attempt.receipt_id,
            "provider_response_receipt_id": (
                None if response_receipt is None else response_receipt.receipt_id
            ),
            "response_validation_id": (None if validation is None else validation.validation_id),
            "outer_diagnostic_id": (
                None if outer_diagnostic is None else outer_diagnostic.diagnostic_id
            ),
            "status": status,
            "reason_code": reason_code,
            "qualification": "unattested_shadow_not_formal",
        }
    )


def verify_deepseek_shadow_run_artifacts(
    artifacts: AITagShadowRunArtifacts,
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs,
    raw_response_body: bytes | None,
) -> None:
    """Trusted rebuild verifier for the complete, still non-formal shadow graph."""

    trusted_plan_inputs.verify_plan(plan)
    envelope = trusted_plan_inputs.envelope
    _verify_plan_envelope(plan=plan, envelope=envelope)
    verify_ai_tag_dispatch_attempt_receipt(
        artifacts.attempt_receipt,
        plan=plan,
        claims=claims,
    )
    attempt = artifacts.attempt_receipt
    observation = AITagShadowExecutionObservationV2.model_validate(
        artifacts.observation.model_dump(mode="json")
    )
    if (
        observation.plan_id != plan.plan_id
        or observation.claims_id != claims.claims_id
        or observation.attempt_receipt_id != attempt.receipt_id
    ):
        raise ValueError("shadow observation graph differs from plan, claims, or attempt")
    response_receipt = artifacts.provider_response_receipt
    validation = artifacts.response_validation
    outer_diagnostic = artifacts.outer_response_diagnostic
    if (
        observation.provider_response_receipt_id
        != (None if response_receipt is None else response_receipt.receipt_id)
        or observation.response_validation_id
        != (None if validation is None else validation.validation_id)
        or observation.outer_diagnostic_id
        != (None if outer_diagnostic is None else outer_diagnostic.diagnostic_id)
    ):
        raise ValueError("shadow observation graph differs from response artifacts")

    if attempt.transport_status != "response_received":
        if (
            raw_response_body is not None
            or response_receipt is not None
            or validation is not None
            or outer_diagnostic is not None
        ):
            raise ValueError("transport failure cannot carry raw or parsed response artifacts")
        if observation.status != attempt.transport_status:
            raise ValueError("transport failure observation status differs from attempt")
        return

    if raw_response_body is None:
        raise ValueError("HTTP response verification requires the original response bytes")
    raw_hash = "sha256:" + hashlib.sha256(raw_response_body).hexdigest()
    if attempt.response_body_sha256 != raw_hash or attempt.response_body_size_bytes != len(
        raw_response_body
    ):
        raise ValueError("attempt receipt differs from original response bytes")
    if attempt.http_status != 200:
        if attempt.http_status is None:
            raise ValueError("response-received attempt is missing HTTP status")
        expected_status, expected_reason = _http_failure(attempt.http_status)
        if response_receipt is not None or validation is not None or outer_diagnostic is not None:
            raise ValueError("non-200 response cannot carry parsed completion artifacts")
        if observation.status != expected_status or observation.reason_code != expected_reason:
            raise ValueError("HTTP failure observation differs from attempt status")
        return

    if response_receipt is None:
        if validation is not None:
            raise ValueError("outer-invalid response cannot carry inner validation")
        if outer_diagnostic is None:
            raise ValueError("outer-invalid response requires a structural diagnostic")
        outer_diagnostic = DeepSeekOuterResponseDiagnostic.model_validate(
            outer_diagnostic.model_dump(mode="json")
        )
        if (
            outer_diagnostic.plan_id != plan.plan_id
            or outer_diagnostic.response_body_sha256 != raw_hash
            or outer_diagnostic.response_body_size_bytes != len(raw_response_body)
        ):
            raise ValueError("outer diagnostic differs from plan or response bytes")
        if (
            observation.status != "invalid_output"
            or observation.reason_code != "provider_outer_contract_invalid"
        ):
            raise ValueError("outer-invalid observation status is inconsistent")
        try:
            parse_deepseek_chat_completion(
                raw_response_body,
                plan=plan,
                latency_ms=attempt.latency_ms,
            )
        except DeepSeekOuterResponseError as exc:
            if exc.diagnostic != outer_diagnostic:
                raise ValueError(
                    "outer diagnostic differs from trusted raw-response rebuild"
                ) from None
            return
        raise ValueError("parseable provider response cannot be marked outer-invalid")

    if outer_diagnostic is not None:
        raise ValueError("parsed provider response cannot carry an outer diagnostic")
    if validation is None:
        raise ValueError("observed provider response requires inner validation")
    verify_deepseek_observed_provider_response_receipt(
        response_receipt,
        plan=plan,
        attempt_receipt=attempt,
        raw_body=raw_response_body,
    )
    verify_ai_tag_response_validation(validation, envelope)
    parsed = parse_deepseek_chat_completion(
        raw_response_body,
        plan=plan,
        latency_ms=attempt.latency_ms,
    )
    expected_validation = validate_ai_tag_completion(
        envelope,
        parsed.raw_completion,
    )
    if validation != expected_validation:
        raise ValueError("inner validation differs from trusted raw-response rebuild")
    expected_system_fingerprint = response_receipt.system_fingerprint or "not_reported"
    raw_usage = response_receipt.usage
    usage_values: tuple[int | None, int | None, int | None] = (
        None,
        None,
        None,
    )
    if raw_usage is not None:
        candidate_usage = (
            raw_usage.prompt_tokens,
            raw_usage.completion_tokens,
            raw_usage.prompt_cache_hit_tokens,
        )
        if all(value is not None for value in candidate_usage):
            usage_values = candidate_usage
    if (
        validation.raw_content_sha256 != response_receipt.content_sha256
        or validation.model != response_receipt.model
        or validation.system_fingerprint != expected_system_fingerprint
        or validation.finish_reason != response_receipt.finish_reason
        or validation.latency_ms != attempt.latency_ms
        or validation.attempt_count != 1
        or validation.usage.input_tokens != usage_values[0]
        or validation.usage.output_tokens != usage_values[1]
        or validation.usage.cache_read_input_tokens != usage_values[2]
        or observation.status != validation.status
        or observation.reason_code != validation.reason_code
    ):
        raise ValueError("inner validation differs from observed provider response")


def _finalize_run_artifacts(
    artifacts: AITagShadowRunArtifacts,
    *,
    plan: AITagShadowDispatchPlan,
    claims: AITagShadowDispatchClaims,
    trusted_plan_inputs: AITagShadowTrustedPlanInputs,
    raw_response_body: bytes | None,
) -> AITagShadowRunArtifacts:
    verify_deepseek_shadow_run_artifacts(
        artifacts,
        plan=plan,
        claims=claims,
        trusted_plan_inputs=trusted_plan_inputs,
        raw_response_body=raw_response_body,
    )
    return artifacts


__all__ = [
    "AITagBudgetReservationLedger",
    "AITagEgressApprovalVerifier",
    "AITagShadowAuthorizationError",
    "AITagShadowAuthorizationGate",
    "AITagShadowDispatchCapability",
    "AITagShadowRunArtifacts",
    "AITagShadowTrustedPlanInputs",
    "AuthorizationFailureReason",
    "DeepSeekShadowRunner",
    "DenyAllAITagBudgetReservationLedger",
    "DenyAllAITagEgressApprovalVerifier",
    "verify_deepseek_shadow_run_artifacts",
]
