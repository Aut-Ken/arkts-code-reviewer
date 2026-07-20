from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn

from arkts_code_reviewer.code_analysis.change_set import CodeSourceSnapshot
from arkts_code_reviewer.code_analysis.context_planning import ContextPlanResult
from arkts_code_reviewer.code_analysis.models import AnalysisResult
from arkts_code_reviewer.hybrid_analysis.builders import (
    PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION,
    AnalysisCardBuilder,
    AnalysisContextPolicy,
)
from arkts_code_reviewer.hybrid_analysis.formal_execution import (
    AITagFormalExecutionEvidenceV2,
    AITagFormalExecutionVerifierV2,
    VerifiedAITagFormalExecutionEligibility,
)
from arkts_code_reviewer.retrieval.models import RetrievalRequest, TargetPlatform
from arkts_code_reviewer.retrieval.query_planner import build_retrieval_request
from arkts_code_reviewer.retrieval.request_v2 import (
    VECTOR_QUERY_POLICY_V1,
    UnitExactSignalsV2,
    candidate_dimension_ids_for_ai_tags,
)
from arkts_code_reviewer.retrieval.request_v3 import (
    RetrievalRequestV3,
    RetrievalUnitRequestV3,
)


class _ImmutableRuntimeObject:
    __slots__ = ("_runtime_sealed",)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_runtime_sealed", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__delattr__(self, name)

    def _seal_runtime_object(self) -> None:
        object.__setattr__(self, "_runtime_sealed", True)


class VerifiedRetrievalRequestV3(_ImmutableRuntimeObject):
    """Opaque runtime wrapper; serialized Request V3 alone is not execution authority."""

    __slots__ = ("_baseline_request", "_eligibilities", "_request")

    def __init__(
        self,
        *,
        request: RetrievalRequestV3,
        baseline_request: RetrievalRequest,
        eligibilities: tuple[VerifiedAITagFormalExecutionEligibility, ...],
        construction_token: object,
    ) -> None:
        if construction_token is not _VERIFIED_REQUEST_TOKEN:
            raise TypeError("verified Retrieval V3 requests can only be built by the gate")
        if type(baseline_request) is not RetrievalRequest:
            raise TypeError("verified Retrieval V3 requires an exact V1 baseline")
        self._request = RetrievalRequestV3.model_validate(request.model_dump(mode="json"))
        self._baseline_request = RetrievalRequest.model_validate(
            baseline_request.model_dump(mode="json")
        )
        self._eligibilities = tuple(eligibilities)
        self._verify_baseline_binding()
        self._verify_proof_binding()
        self._seal_runtime_object()

    def _verify_baseline_binding(self) -> None:
        request = self._request
        baseline = self._baseline_request
        if (
            request.context_plan_id != baseline.context_plan_id
            or request.feature_routing_id != baseline.feature_routing_id
            or request.feature_config_version != baseline.feature_config_version
            or request.index_version != baseline.index_version
            or request.target_platform != baseline.target_platform
            or request.total_knowledge_token_budget != baseline.total_knowledge_token_budget
            or len(request.units) != len(baseline.units)
        ):
            raise ValueError("verified Retrieval V3 differs from its V1 request baseline")
        for unit, baseline_unit in zip(request.units, baseline.units, strict=True):
            if (
                unit.unit_id != baseline_unit.unit_id
                or unit.source_ref_id != baseline_unit.source_ref_id
                or unit.profile_id != baseline_unit.profile_id
                or unit.review_question_ids != baseline_unit.review_question_ids
                or unit.dispatchable_review_question_ids
                != baseline_unit.dispatchable_review_question_ids
                or unit.exact_signals.model_dump(exclude={"import_uses"})
                != baseline_unit.exact_signals.model_dump()
                or unit.exact_tags != baseline_unit.exact_tags
                or unit.routing_tags != baseline_unit.routing_tags
                or unit.retrieval_dimension_ids != baseline_unit.retrieval_dimension_ids
                or unit.routing_dimension_ids != baseline_unit.routing_dimension_ids
                or unit.requested_rule_ids != baseline_unit.requested_rule_ids
                or unit.semantic_code_excerpt != baseline_unit.semantic_code_excerpt
                or unit.intent_summary != baseline_unit.intent_summary
                or unit.quality != baseline_unit.quality
                or unit.knowledge_token_budget != baseline_unit.knowledge_token_budget
            ):
                raise ValueError("verified Retrieval V3 Unit differs from V1 baseline")

    def _verify_proof_binding(self) -> None:
        if len(self._eligibilities) != len(self._request.units):
            raise ValueError("verified Retrieval V3 proof coverage differs from request")
        for unit, eligibility in zip(
            self._request.units,
            self._eligibilities,
            strict=True,
        ):
            if type(eligibility) is not VerifiedAITagFormalExecutionEligibility:
                raise TypeError("verified Retrieval V3 requires exact formal eligibility proofs")
            expected_disagreements = tuple(
                state.tag_id
                for state in eligibility.hybrid.tag_states
                if state.unit_comparison_status == "disagreement"
            )
            if (
                eligibility.unit_id != unit.unit_id
                or eligibility.subject_id != unit.trusted_execution_subject_id
                or eligibility.attestation_id != unit.trusted_runner_attestation_id
                or eligibility.outcome.outcome_id != unit.formal_execution_outcome_id
                or eligibility.hybrid.analysis_id != unit.formal_hybrid_analysis_id
                or (None if eligibility.result is None else eligibility.result.result_id)
                != unit.formal_ai_result_id
                or eligibility.positive_tags != unit.ai_inferred_tags
                or expected_disagreements != unit.tag_disagreements
                or candidate_dimension_ids_for_ai_tags(eligibility.positive_tags)
                != unit.candidate_dimension_ids
            ):
                raise ValueError("verified Retrieval V3 proof identity differs from request")

    def __repr__(self) -> str:
        return (
            "VerifiedRetrievalRequestV3("
            f"request_id={self._request.request_id!r}, formal_proofs=<opaque>)"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("verified Retrieval V3 requests are not serializable")

    @property
    def request(self) -> RetrievalRequestV3:
        self._verify_baseline_binding()
        self._verify_proof_binding()
        return self._request

    @property
    def formal_attestation_ids(self) -> tuple[str, ...]:
        self._verify_baseline_binding()
        self._verify_proof_binding()
        return tuple(item.attestation_id for item in self._eligibilities)


_VERIFIED_REQUEST_TOKEN = object()


def _normalize_evidence(
    value: Mapping[str, AITagFormalExecutionEvidenceV2],
) -> dict[str, AITagFormalExecutionEvidenceV2]:
    if not isinstance(value, Mapping):
        raise TypeError("formal_evidence_by_unit must be a mapping")
    normalized: dict[str, AITagFormalExecutionEvidenceV2] = {}
    for unit_id, evidence in value.items():
        if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
            raise ValueError("Formal evidence Unit IDs must be non-empty and trimmed")
        if type(evidence) is not AITagFormalExecutionEvidenceV2:
            raise TypeError("formal evidence values must use Formal Execution Evidence V2")
        if unit_id in normalized:
            raise ValueError("Formal evidence Unit IDs must be unique")
        normalized[unit_id] = evidence
    return normalized


class TrustedRetrievalRequestV3Builder(_ImmutableRuntimeObject):
    """Build Request V3 only through a deployment-configured formal verifier.

    V3 remains a structural shadow request and is not accepted by the current
    RetrievalService. The returned runtime wrapper prevents a self-hashed JSON
    request from being mistaken for a freshly verified execution authority.
    """

    __slots__ = ("_analysis_context_policy", "_formal_execution_verifier")

    def __init__(
        self,
        *,
        formal_execution_verifier: AITagFormalExecutionVerifierV2,
        analysis_context_policy: AnalysisContextPolicy,
    ) -> None:
        if type(formal_execution_verifier) is not AITagFormalExecutionVerifierV2:
            raise TypeError("Retrieval V3 Builder requires a formal execution verifier")
        if not isinstance(analysis_context_policy, AnalysisContextPolicy):
            raise TypeError("Retrieval V3 Builder requires an Analysis Context policy")
        if analysis_context_policy.builder_version != PROVIDER_EGRESS_ANALYSIS_CARD_BUILDER_VERSION:
            raise ValueError("Retrieval V3 requires the provider-egress Analysis Card policy")
        self._formal_execution_verifier = formal_execution_verifier
        self._analysis_context_policy = analysis_context_policy
        self._seal_runtime_object()

    def __repr__(self) -> str:
        return (
            "TrustedRetrievalRequestV3Builder("
            f"registry_id={self._formal_execution_verifier.registry_id!r})"
        )

    def build(
        self,
        analysis_result: AnalysisResult,
        context_plan: ContextPlanResult,
        *,
        source_snapshots: Mapping[str, CodeSourceSnapshot],
        formal_evidence_by_unit: Mapping[str, AITagFormalExecutionEvidenceV2],
        target_platform: TargetPlatform,
        resolved_index_version: str,
        knowledge_token_budget: int,
        requested_rule_ids_by_unit: Mapping[str, tuple[str, ...]] | None = None,
    ) -> VerifiedRetrievalRequestV3:
        if not isinstance(source_snapshots, Mapping):
            raise TypeError("source_snapshots must be a mapping")
        baseline = build_retrieval_request(
            analysis_result,
            context_plan,
            target_platform=target_platform,
            resolved_index_version=resolved_index_version,
            knowledge_token_budget=knowledge_token_budget,
            requested_rule_ids_by_unit=requested_rule_ids_by_unit,
        )
        evidence_by_unit = _normalize_evidence(formal_evidence_by_unit)
        unit_ids = tuple(item.unit_id for item in baseline.units)
        if set(evidence_by_unit) != set(unit_ids):
            raise ValueError("Formal evidence must exactly cover Retrieval primary Units")

        expected_cards = AnalysisCardBuilder().build_many(
            analysis_result=analysis_result,
            context_plan=context_plan,
            source_snapshots=source_snapshots,
            unit_ids=unit_ids,
            policy=self._analysis_context_policy,
        )
        cards_by_unit = {item.unit_id: item for item in expected_cards}
        if set(cards_by_unit) != set(unit_ids):
            raise ValueError("trusted Analysis Card rebuild does not cover Retrieval Units")
        scopes_by_unit = {item.unit_id: item for item in analysis_result.unit_fact_scopes}

        eligibilities: list[VerifiedAITagFormalExecutionEligibility] = []
        units: list[RetrievalUnitRequestV3] = []
        for baseline_unit in baseline.units:
            unit_id = baseline_unit.unit_id
            eligibility = self._formal_execution_verifier.verify(evidence_by_unit[unit_id])
            card = cards_by_unit[unit_id]
            if eligibility.unit_id != unit_id or eligibility.card_id != card.card_id:
                raise ValueError("Formal eligibility differs from trusted upstream Card")
            formal_card = evidence_by_unit[unit_id].trusted_plan_inputs.card
            if formal_card != card:
                raise ValueError("Formal evidence Card differs from trusted upstream rebuild")
            if (
                card.source_ref_id != baseline_unit.source_ref_id
                or card.feature_profile_id != baseline_unit.profile_id
                or card.feature_routing_id != baseline.feature_routing_id
                or card.context_plan_id != baseline.context_plan_id
                or card.feature_config_fingerprint != baseline.feature_config_version
            ):
                raise ValueError("Formal Card identities differ from V1 Retrieval baseline")
            scope = scopes_by_unit.get(unit_id)
            if scope is None or scope.source_ref_id != baseline_unit.source_ref_id:
                raise ValueError("Retrieval V3 Unit has no aligned UnitFactScope")
            exact = scope.unit_exact
            exact_signals = UnitExactSignalsV2(
                apis=exact.apis,
                components=exact.components,
                decorators=exact.decorators,
                attributes=exact.attributes,
                symbols=exact.symbols,
                syntax=exact.syntax,
                calls=exact.calls,
                import_uses=exact.import_uses,
                resource_references=exact.resource_references,
            )
            if exact_signals.model_dump(exclude={"import_uses"}) != (
                baseline_unit.exact_signals.model_dump()
            ):
                raise ValueError("Retrieval V3 exact facts do not preserve V1 baseline")

            hybrid = eligibility.hybrid
            ai_inferred_tags = eligibility.positive_tags
            tag_disagreements = tuple(
                state.tag_id
                for state in hybrid.tag_states
                if state.unit_comparison_status == "disagreement"
            )
            candidate_dimension_ids = candidate_dimension_ids_for_ai_tags(ai_inferred_tags)
            units.append(
                RetrievalUnitRequestV3(
                    unit_id=unit_id,
                    source_ref_id=baseline_unit.source_ref_id,
                    profile_id=baseline_unit.profile_id,
                    formal_hybrid_analysis_id=hybrid.analysis_id,
                    formal_execution_outcome_id=eligibility.outcome.outcome_id,
                    formal_ai_result_id=(
                        None if eligibility.result is None else eligibility.result.result_id
                    ),
                    trusted_execution_subject_id=eligibility.subject_id,
                    trusted_runner_attestation_id=eligibility.attestation_id,
                    ai_signal_scope=("attestation_bound_requires_runtime_verified_wrapper"),
                    review_question_ids=baseline_unit.review_question_ids,
                    dispatchable_review_question_ids=(
                        baseline_unit.dispatchable_review_question_ids
                    ),
                    exact_signals=exact_signals,
                    exact_tags=baseline_unit.exact_tags,
                    routing_tags=baseline_unit.routing_tags,
                    ai_inferred_tags=ai_inferred_tags,
                    tag_disagreements=tag_disagreements,
                    retrieval_dimension_ids=baseline_unit.retrieval_dimension_ids,
                    routing_dimension_ids=baseline_unit.routing_dimension_ids,
                    candidate_dimension_ids=candidate_dimension_ids,
                    requested_rule_ids=baseline_unit.requested_rule_ids,
                    semantic_code_excerpt=baseline_unit.semantic_code_excerpt,
                    intent_summary=baseline_unit.intent_summary,
                    vector_query_policy=VECTOR_QUERY_POLICY_V1,
                    quality=baseline_unit.quality,
                    knowledge_token_budget=baseline_unit.knowledge_token_budget,
                )
            )
            eligibilities.append(eligibility)

        request = RetrievalRequestV3.create(
            context_plan_id=baseline.context_plan_id,
            feature_routing_id=baseline.feature_routing_id,
            feature_config_version=baseline.feature_config_version,
            index_version=baseline.index_version,
            target_platform=baseline.target_platform,
            total_knowledge_token_budget=baseline.total_knowledge_token_budget,
            units=tuple(units),
        )
        return VerifiedRetrievalRequestV3(
            request=request,
            baseline_request=baseline,
            eligibilities=tuple(eligibilities),
            construction_token=_VERIFIED_REQUEST_TOKEN,
        )


__all__ = [
    "TrustedRetrievalRequestV3Builder",
    "VerifiedRetrievalRequestV3",
]
